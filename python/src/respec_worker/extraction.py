"""Pass 1 of extraction: the prompt, the reply schema and the reply parser.

Ported from Specter's ``api/extraction/schemas.py`` and ``client.py`` (commit
17a94aa). Only Pass 1, entity extraction, is here; Specter's Pass 2 and 3 and
their reshaping are S3's. The prompt files are copied as they were.
"""

from importlib.resources import files
from typing import Annotated, Any, Final, Literal

import orjson
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

PASS1_MAX_TOKENS: Final = 16384
"""The output-token budget for a Pass 1 call, until S3 derives it from the Model.

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


def load_prompt(name: str) -> str:
    """Return the text of the prompt file ``prompts/<name>`` in this package."""
    return files("respec_worker").joinpath("prompts", name).read_text("utf-8")


def pass1_messages(body: str) -> list[dict[str, str]]:
    """The chat messages for one Pass 1 call over the Document text ``body``."""
    user = load_prompt("pass1_entities.md").replace("{body}", body)
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
    """One entity Proposal from Pass 1, with the sentences that ground it."""

    model_config = ConfigDict(extra="forbid")

    label: EntityLabel
    name: Annotated[str, StringConstraints(min_length=1, max_length=_NAME_MAX)]
    supporting_sentences: SupportingSentences
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _drop_emitted_id(cls, data: Any) -> Any:
        """Drop an ``id`` the Model made up; the prompt says not to invent ids."""
        if isinstance(data, dict) and "id" in data:
            data = {k: v for k, v in data.items() if k != "id"}
        return data


class Pass1Response(BaseModel):
    """The whole Pass 1 reply: a list of entity Proposals."""

    model_config = ConfigDict(extra="forbid")

    entities: list[Pass1EntityProposal]


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


def parse_pass1(raw: str) -> Pass1Response:
    """Parse and validate a Model's Pass 1 reply.

    Raises ``BadOutput`` when the reply is empty, holds no complete JSON
    document, is not valid JSON, or does not fit the Pass 1 schema.
    """
    document = _strip_json_envelope(raw) if raw.strip() else None
    if document is None:
        raise BadOutput(
            "The Model's reply held no complete JSON; it may have been cut "
            "off. Try again, or later with another Model."
        )
    try:
        return Pass1Response.model_validate(orjson.loads(document))
    except orjson.JSONDecodeError as err:
        raise BadOutput(
            "The Model's reply was not valid JSON; try again, or later with "
            "another Model."
        ) from err
    except ValidationError as err:
        raise BadOutput(
            "The Model's reply did not match the entity format; try again, or "
            "later with another Model."
        ) from err
