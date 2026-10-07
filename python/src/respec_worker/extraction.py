"""Both extraction passes: the prompts, the reply schemas and the reply parsers.

Pass 1 proposes Entities, Events included, and Pass 2 proposes Relationships
between them, including who took part in each Event. There is no third pass
(ADR 0004). Ported from Specter's ``api/extraction/schemas.py`` and
``client.py`` (commit 17a94aa), minus its Pass 3 and its event-promotion flags.
"""

import datetime
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Annotated, Any, Final, Literal, Self

import orjson
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import ErrorDetails

_LOGGER = logging.getLogger(__name__)

PASS1_MAX_TOKENS: Final = 16384
"""The output-token budget for each pass's call, until S3 derives it from the Model.

Specter's fixed 4,096 truncated a 28k-character article; the 2026-10-01 probe
needed about 8k. Both default Models allow more: OpenRouter's
nemotron-3-super lists 235,929, and Gemini's flash-lite alias 65,536.
"""

_NAME_MAX: Final = 200
_SENTENCE_MAX: Final = 2_000

EntityLabel = Literal[
    "Person", "Organization", "Identity", "Vessel", "Location", "Event"
]
"""The kinds of entity the Pass 1 prompt allows."""

RelationshipType = Literal[
    "WORKS_FOR",
    "OWNS",
    "CONTROLS",
    "MEMBER_OF",
    "ASSOCIATED_WITH",
    "FAMILY_OF",
    "BORN_IN",
    "LOCATED_IN",
    "HEADQUARTERED_IN",
    "OPERATES_IN",
    "TRAVELED_TO",
    "PARTICIPATED_IN",
    "REGISTERED_TO",
    "FLAGGED_BY",
    "DOCUMENTS",
]
"""The types Pass 2 may propose: Specter's extractable list, which leaves out the
structural types its routes wrote. ``PARTICIPATED_IN`` links a participant to an
Event."""

DatePrecision = Literal["exact", "day", "month", "year", "range", "unknown"]
"""How exactly a date is known, as in Specter."""

_PARTIAL_DATE: Final = re.compile(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?")


def _period_start(value: str) -> datetime.date:
    """The first day of the period ``value`` names: ``2024-02`` is 2024-02-01.

    Raises ``ValueError`` when the month or day does not exist.
    """
    parts = [int(part) for part in value.split("-")]
    parts += [1] * (3 - len(parts))
    return datetime.date(*parts)


def _check_partial_date(value: str) -> str:
    if not _PARTIAL_DATE.fullmatch(value):
        raise ValueError("a date is written YYYY, YYYY-MM or YYYY-MM-DD")
    _period_start(value)
    return value


PartialDate = Annotated[str, AfterValidator(_check_partial_date)]
"""An ISO 8601 date that may stop at the year or the month."""


def load_prompt(name: str) -> str:
    """Return the text of the prompt file ``prompts/<name>`` in this package."""
    return files("respec_worker").joinpath("prompts", name).read_text("utf-8")


def _fill(template: str, values: Mapping[str, str]) -> str:
    """``template`` with each ``{name}`` replaced from ``values`` in one pass, so
    a placeholder inside a value is left as text."""
    return re.sub(r"\{(\w+)\}", lambda match: values[match.group(1)], template)


def pass1_messages(body: str) -> list[dict[str, str]]:
    """The chat messages for one Pass 1 call over the Document text ``body``."""
    user = _fill(load_prompt("pass1_entities.md"), {"body": body})
    return [
        {"role": "system", "content": load_prompt("system.md")},
        {"role": "user", "content": user},
    ]


def _clip_supporting_sentences(value: Any) -> Any:
    """Cut an over-long ``supporting_sentences`` list down to the schema's caps.

    A third sentence or a sentence over the cap is trimmed instead of failing
    the whole reply (two real articles each gave one entity three sentences).
    Anything that is not a list passes through, so the normal checks still
    report malformed input.
    """
    if not isinstance(value, list):
        return value
    return [s[:_SENTENCE_MAX] if isinstance(s, str) else s for s in value[:2]]


SupportingSentences = Annotated[
    list[Annotated[str, StringConstraints(min_length=1, max_length=_SENTENCE_MAX)]],
    BeforeValidator(_clip_supporting_sentences),
    Field(min_length=1, max_length=2),
]


class Pass1EntityProposal(BaseModel):
    """One entity Proposal from Pass 1, with the sentences that ground it.

    Only an Event has a ``date`` and a ``place``, and each is null when the
    article does not say. The date's form (YYYY, YYYY-MM or YYYY-MM-DD) is its
    precision. A key the schema does not have, such as an ``id`` the Model made
    up, is ignored.
    """

    model_config = ConfigDict(extra="ignore")

    label: EntityLabel
    name: Annotated[str, StringConstraints(min_length=1, max_length=_NAME_MAX)]
    supporting_sentences: SupportingSentences
    attributes: dict[str, Any] = Field(default_factory=dict)
    date: PartialDate | None = None
    place: (
        Annotated[str, StringConstraints(min_length=1, max_length=_NAME_MAX)] | None
    ) = None

    @model_validator(mode="after")
    def _only_an_event_has_a_date_and_place(self) -> Self:
        if self.label != "Event" and (self.date is not None or self.place is not None):
            raise ValueError("only an Event has a date or a place")
        return self


@dataclass(frozen=True, slots=True)
class Pass1Response:
    """What a Pass 1 reply held: the entity Proposals that validated, and how
    many items were dropped for being malformed."""

    entities: list[Pass1EntityProposal]
    dropped: int


class Pass2RelationshipProposal(BaseModel):
    """One relationship Proposal from Pass 2: a typed link from one Pass 1 entity
    to another, named by the ids the worker stamped, and the sentences that
    ground it. For ``PARTICIPATED_IN`` the ``to_id`` is the Event."""

    model_config = ConfigDict(extra="ignore")

    type: RelationshipType
    from_id: str
    to_id: str
    date_from: PartialDate | None = None
    date_to: PartialDate | None = None
    date_precision: DatePrecision
    supporting_sentences: SupportingSentences

    @model_validator(mode="after")
    def _check_ends_differ(self) -> Self:
        if self.from_id == self.to_id:
            raise ValueError("a relationship needs two different entities")
        return self

    @model_validator(mode="after")
    def _check_dates_in_order(self) -> Self:
        if (
            self.date_from is not None
            and self.date_to is not None
            and _period_start(self.date_to) < _period_start(self.date_from)
        ):
            raise ValueError("date_to must not be before date_from")
        return self


@dataclass(frozen=True, slots=True)
class Pass2Response:
    """What a Pass 2 reply held: the relationship Proposals that validated, and
    how many items were dropped for being malformed."""

    relationships: list[Pass2RelationshipProposal]
    dropped: int


class BadOutput(Exception):
    """A Model reply that is not valid Pass 1 JSON.

    ``message`` is one plain sentence with a next step. It holds no part of the
    reply, so it is safe to show and to log.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _scan_balanced(text: str) -> str | None:
    """Return the first balanced ``{...}`` or ``[...]`` document in ``text``.

    Braces inside string literals do not count, and an escaped quote does not
    end a string. Returns None when there is no opening brace or it never
    closes, which is what a reply cut off by the token cap looks like.
    """
    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 1  # skip the escaped character
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def _strip_json_envelope(raw: str) -> str | None:
    """Return the first JSON document inside ``raw``, whatever wraps it.

    Tolerated: a ``<json>...</json>`` pair, a markdown fence (with or without a
    language tag), or bare JSON. Prose before or after the document is dropped.
    """
    tagged = raw.find("<json>")
    if tagged != -1:
        end = raw.find("</json>", tagged)
        if end != -1:
            return _scan_balanced(raw[tagged + len("<json>") : end])
    fence = raw.find("```")
    if fence != -1:
        inside = raw[fence + 3 :]
        newline = inside.find("\n")
        if newline != -1:
            inside = inside[newline + 1 :]
        end = inside.find("```")
        if end != -1:
            return _scan_balanced(inside[:end])
    return _scan_balanced(raw)


def _where(error: ErrorDetails) -> str:
    """Where an item went wrong and what kind of error it was, from the schema
    alone: a field name and an error type, never anything the Model wrote."""
    field = ".".join(str(part) for part in error["loc"]) or "item"
    return f"{field} ({error['type']})"


def _trim_keys(item: object) -> object:
    """``item`` with the whitespace around its keys removed, so a key the Model
    padded, such as ``" supporting_sentences"``, is the field it was meant to
    be. Nothing else about a key is repaired."""
    if not isinstance(item, dict):
        return item
    return {key.strip(): value for key, value in item.items()}


def _parse_items[Item: BaseModel](
    raw: str, key: str, schema: type[Item], what: str
) -> tuple[list[Item], int]:
    """The items of ``raw``'s ``key`` list that validate as ``schema``, and how
    many did not.

    The whole reply fails with ``BadOutput`` only when it is empty, holds no
    complete JSON document, is not valid JSON, or is not an object with a list
    under ``key``. A key the schema does not have is ignored. An item with a
    missing field or a bad value is dropped and counted, and logged by its
    place in the list, its error types and its fields, never by its content.
    ``what`` names the items in messages.
    """
    document = _strip_json_envelope(raw) if raw.strip() else None
    if document is None:
        raise BadOutput(
            "The Model's reply held no complete JSON; it may have been cut "
            "off. Try again, or later with another Model."
        )
    try:
        reply = orjson.loads(document)
    except orjson.JSONDecodeError as err:
        raise BadOutput(
            "The Model's reply was not valid JSON; try again, or later with "
            "another Model."
        ) from err
    items = reply.get(key) if isinstance(reply, dict) else None
    if not isinstance(items, list):
        raise BadOutput(
            f"The Model's reply did not match the {what} format; try again, or "
            "later with another Model."
        )
    kept: list[Item] = []
    for number, item in enumerate(items, start=1):
        try:
            kept.append(schema.model_validate(_trim_keys(item)))
        except ValidationError as err:
            _LOGGER.error(
                "Dropped %s %d: %s",
                what,
                number,
                "; ".join(_where(error) for error in err.errors()),
            )
    return kept, len(items) - len(kept)


def parse_pass1(raw: str) -> Pass1Response:
    """Parse a Model's Pass 1 reply, dropping malformed entities, or raise
    ``BadOutput`` when the reply as a whole is unusable."""
    entities, dropped = _parse_items(raw, "entities", Pass1EntityProposal, "entity")
    return Pass1Response(entities, dropped)


def parse_pass2(raw: str) -> Pass2Response:
    """Parse a Model's Pass 2 reply, dropping malformed relationships, or raise
    ``BadOutput`` when the reply as a whole is unusable."""
    relationships, dropped = _parse_items(
        raw, "relationships", Pass2RelationshipProposal, "relationship"
    )
    return Pass2Response(relationships, dropped)


def stamp_ids(response: Pass1Response) -> dict[str, Pass1EntityProposal]:
    """The Pass 1 entities keyed by the short ids ``e1``, ``e2``, ... in the
    Model's order. The worker stamps them; the Model never makes one up."""
    return {f"e{n}": entity for n, entity in enumerate(response.entities, start=1)}


def _listed(entity_id: str, entity: Pass1EntityProposal) -> dict[str, str]:
    """What Pass 2 is told about an entity: its id, kind and name, and an
    Event's date and place when it has them."""
    listed = {"id": entity_id, "label": entity.label, "name": entity.name}
    if entity.date is not None:
        listed["date"] = entity.date
    if entity.place is not None:
        listed["place"] = entity.place
    return listed


def pass2_messages(
    body: str, entities: Mapping[str, Pass1EntityProposal]
) -> list[dict[str, str]]:
    """The chat messages for one Pass 2 call over the Document text ``body`` and
    the Pass 1 ``entities`` keyed by their ids."""
    listing = "\n".join(
        orjson.dumps(_listed(entity_id, entity)).decode()
        for entity_id, entity in entities.items()
    )
    user = _fill(
        load_prompt("pass2_relationships.md"), {"entities": listing, "body": body}
    )
    return [
        {"role": "system", "content": load_prompt("system.md")},
        {"role": "user", "content": user},
    ]


def _resolves(
    link: Pass2RelationshipProposal, entities: Mapping[str, Pass1EntityProposal]
) -> bool:
    """Whether both ends of ``link`` are known entities and, for a
    ``PARTICIPATED_IN``, it goes from a non-Event to an Event."""
    source, target = entities.get(link.from_id), entities.get(link.to_id)
    if source is None or target is None:
        return False
    if link.type == "PARTICIPATED_IN":
        return target.label == "Event" and source.label != "Event"
    return True


def known_relationships(
    response: Pass2Response, entities: Mapping[str, Pass1EntityProposal]
) -> tuple[list[Pass2RelationshipProposal], int]:
    """The Proposals in ``response`` that resolve against ``entities`` (keyed by
    their ids), and how many items of the reply were dropped in all: the
    malformed ones, those naming an id Pass 1 never gave, and participant links
    that do not go from a non-Event to an Event."""
    kept = [link for link in response.relationships if _resolves(link, entities)]
    return kept, response.dropped + len(response.relationships) - len(kept)
