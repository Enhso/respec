"""Pydantic response models for the three Claude extraction passes.

Each pass returns one strict JSON document; this module validates it.
Failures raise :class:`pydantic.ValidationError` (the orchestrator wraps
them as :class:`api.extraction.client.ExtractionResponseError`). Cross-
field validation that spans two passes (e.g. "Pass-2 ``from_id`` must be
a known Pass-1 entity id") lives at the pipeline layer because a model
validator cannot see the prior pass's output without unpleasant state
passing.

The three top-level shapes — :class:`Pass1Response`,
:class:`Pass2Response`, :class:`Pass3Response` — each wrap a single
list of proposals. The list is allowed to be empty for passes 2 and 3
(an article can yield zero relationships or no event-promotable tuples);
the orchestrator gates the "empty pass 1" case at the response-error
boundary because every extraction must produce at least one entity for
the pipeline to be useful.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

_NAME_MAX: Final[int] = 200
_TITLE_MAX: Final[int] = 500
_DESCRIPTION_MAX: Final[int] = 4_000
_SENTENCE_MAX: Final[int] = 2_000

EntityLabel = Literal[
    "Person", "Organization", "Identity", "Vessel", "Location", "Event"
]
"""§7 entity labels the extractor is permitted to propose.

Meta-labels (``QueueItem``, ``Document``, ``SourceOutlet``,
``IdentityCluster``, ``ActionLog``) are excluded — those are written by
routes, never proposed by Claude.
"""

ExtractableEdgeType = Literal[
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
"""§8 relationship types the extractor is permitted to propose.

The writable subset of :data:`api.models.edges.EDGE_TYPES` — excludes
structural edges (``OPERATES_AS``, ``EXTRACTED_FROM``, ``PUBLISHED_BY``,
``MENTIONS``, ``PRIMARY_SUBJECT``) that are written by routes. A test
in ``tests/extraction/test_schemas.py`` asserts every member is in
``EDGE_TYPES`` so a future addition is caught in CI.
"""

EventType = Literal[
    "meeting", "operation", "transaction", "appointment", "incident", "other"
]
"""§7 event types — mirrors :class:`api.models.nodes.Event.type`."""

EventCandidateReason = Literal[
    "multi_participant", "explicit_location", "singular_instant"
]
"""Discriminator for the §7 event-promotion rule."""

DatePrecision = Literal["exact", "day", "month", "year", "range", "unknown"]
"""Pass 2 / Pass 3 date precision.

Pass 3 narrows to the 5-value set (no ``"unknown"``) at the field level —
once a relationship has been promoted to an Event, at least one date is
asserted.
"""

EventDatePrecision = Literal["exact", "day", "month", "year", "range"]


_SUPPORTING_SENTENCE = Annotated[
    str, StringConstraints(min_length=1, max_length=_SENTENCE_MAX)
]


def _clip_supporting_sentences(value: Any) -> Any:
    """Clip an over-long ``supporting_sentences`` list to the wire caps.

    Runs ``mode="before"`` field validation so a 3+-sentence array or a
    sentence over :data:`_SENTENCE_MAX` chars degrades to the cap
    instead of failing the whole pass closed (epic 39 Phase-B lead 1:
    two real Bellingcat articles each produced a 3-sentence array for
    one recurring entity, rejecting an otherwise-valid proposal).
    Non-list input passes through unchanged so the normal
    ``min_length=1`` / type checks still report the real error on
    malformed input.
    """
    if not isinstance(value, list):
        return value
    return [s[:_SENTENCE_MAX] if isinstance(s, str) else s for s in value[:2]]


SupportingSentences = Annotated[
    list[_SUPPORTING_SENTENCE],
    BeforeValidator(_clip_supporting_sentences),
    Field(min_length=1, max_length=2),
]


class Pass1EntityProposal(BaseModel):
    """One Claude-proposed entity from Pass 1.

    The ``id`` is generated server-side after parsing (Claude returns
    the proposal without an id; the orchestrator stamps a fresh
    :func:`uuid.uuid4` so passes 2 and 3 can reference it). ``label`` is
    restricted to the entity labels the extractor is permitted to
    propose; meta-labels are excluded.

    Attributes:
        id: Server-stamped uuid4.
        label: One of the §7 entity labels.
        name: Candidate entity name (mirrors node-side cap).
        supporting_sentences: One or two sentences pulled verbatim from
            the article body; provenance for the human reviewer.
        attributes: Optional flat dict of label-specific attributes
            (e.g. ``{"nationality": "Russian"}`` for Person). The
            QueueItem payload preserves it verbatim; epic 13's approve
            flow maps it onto the concrete node fields.
    """

    id: UUID = Field(default_factory=uuid4, json_schema_extra={"readOnly": True})
    label: EntityLabel
    name: Annotated[str, StringConstraints(min_length=1, max_length=_NAME_MAX)]
    supporting_sentences: SupportingSentences
    attributes: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _strip_claude_emitted_id(cls, data: Any) -> Any:
        """Drop any Claude-emitted ``id`` so default_factory fires server-side.

        The system prompt instructs Claude not to invent ids; this is
        the structural enforcement so a non-compliant response cannot
        smuggle a uuid through the prompt-cache window and collide on
        ``QueueItem.id``'s UNIQUE constraint.
        """
        if isinstance(data, dict) and "id" in data:
            data = {k: v for k, v in data.items() if k != "id"}
        return data


class Pass1Response(BaseModel):
    """Top-level Pass-1 response — a list of entity proposals."""

    entities: list[Pass1EntityProposal]

    model_config = ConfigDict(extra="forbid")


class Pass2RelationshipProposal(BaseModel):
    """One dyadic relationship between two Pass-1 entities.

    ``event_candidate`` and ``event_candidate_reason`` together carry the
    §7 boundary rule: if Claude judged this tuple to need Event
    promotion, ``event_candidate=True`` and ``event_candidate_reason`` is
    the discriminator. Pass 3 runs only when at least one proposal has
    ``event_candidate=True``.

    Attributes:
        id: Server-stamped uuid4.
        type: §8 relationship type (the extractable subset; structural
            edges are written by routes, not the extractor).
        from_id: UUID of the source entity from Pass 1.
        to_id: UUID of the target entity from Pass 1.
        date_from: Optional start date for the observation that will
            land on the eventual edge.
        date_to: Optional end date.
        date_precision: Required precision label.
        supporting_sentences: Verbatim provenance.
        event_candidate: ``True`` when Claude judged this tuple
            event-promotable per §7.
        event_candidate_reason: Discriminator when ``event_candidate``.
    """

    id: UUID = Field(default_factory=uuid4, json_schema_extra={"readOnly": True})
    type: ExtractableEdgeType
    from_id: UUID
    to_id: UUID
    date_from: date | None = None
    date_to: date | None = None
    date_precision: DatePrecision
    supporting_sentences: SupportingSentences
    event_candidate: bool = False
    event_candidate_reason: EventCandidateReason | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _strip_claude_emitted_id(cls, data: Any) -> Any:
        """Drop any Claude-emitted ``id`` so default_factory fires server-side."""
        if isinstance(data, dict) and "id" in data:
            data = {k: v for k, v in data.items() if k != "id"}
        return data

    @model_validator(mode="after")
    def _check_event_flag_consistency(self) -> Self:
        """Reject ``event_candidate=True`` without a reason and vice versa."""
        if self.event_candidate and self.event_candidate_reason is None:
            raise ValueError("event_candidate=True requires event_candidate_reason.")
        if not self.event_candidate and self.event_candidate_reason is not None:
            raise ValueError("event_candidate_reason requires event_candidate=True.")
        return self

    @model_validator(mode="after")
    def _check_endpoints_distinct(self) -> Self:
        """Reject self-loops — a relationship's two endpoints must differ."""
        if self.from_id == self.to_id:
            raise ValueError(
                "Pass-2 relationship from_id must differ from to_id "
                "(self-loops are not a valid §8 relationship)."
            )
        return self

    @model_validator(mode="after")
    def _check_date_window(self) -> Self:
        """Reject ``date_to`` < ``date_from`` and pair-vs-precision drift."""
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_to < self.date_from
        ):
            raise ValueError(
                "Pass-2 date_to must be greater than or equal to date_from."
            )
        return self


class Pass2Response(BaseModel):
    """Top-level Pass-2 response — a list of relationship proposals."""

    relationships: list[Pass2RelationshipProposal]

    model_config = ConfigDict(extra="forbid")


class Pass3EventProposal(BaseModel):
    """One Event proposal sourced from Pass-2's flagged tuples.

    The event aggregates 3+ participants OR carries an explicit location
    OR has singular-instant semantics, per §7. ``participant_ids`` is
    the set of Pass-1 entity ids playing into the event; ``location_id``
    is optional and references a Pass-1 Location entity when present.
    The ``date_to`` / ``date_precision`` invariant mirrors
    :class:`api.models.nodes.Event` so a future approve-and-write step
    can map this proposal straight onto the node.

    Attributes:
        id: Server-stamped uuid4.
        title: Short event title (mirrors :class:`Event.title` cap).
        type: §7 event type.
        date_from: Required start date.
        date_to: Required when ``date_precision == "range"``, otherwise
            forbidden.
        date_precision: Required precision label.
        location_id: Optional Pass-1 entity id (must reference a
            ``label=="Location"`` proposal; validated cross-field in
            the pipeline).
        participant_ids: At least two Pass-1 entity ids.
        description: Free-form event description.
        supporting_sentences: Verbatim provenance.
    """

    id: UUID = Field(default_factory=uuid4, json_schema_extra={"readOnly": True})
    title: Annotated[str, StringConstraints(min_length=1, max_length=_TITLE_MAX)]
    type: EventType
    date_from: date
    date_to: date | None = None
    date_precision: EventDatePrecision
    location_id: UUID | None = None
    participant_ids: list[UUID] = Field(min_length=2)
    description: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    supporting_sentences: SupportingSentences

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _strip_claude_emitted_id(cls, data: Any) -> Any:
        """Drop any Claude-emitted ``id`` so default_factory fires server-side."""
        if isinstance(data, dict) and "id" in data:
            data = {k: v for k, v in data.items() if k != "id"}
        return data

    @model_validator(mode="after")
    def _check_date_consistency(self) -> Self:
        """Mirror :class:`api.models.nodes.Event._check_date_consistency`."""
        is_range = self.date_precision == "range"
        if is_range and self.date_to is None:
            raise ValueError(
                "Pass3EventProposal.date_to is required when date_precision == 'range'."
            )
        if not is_range and self.date_to is not None:
            raise ValueError(
                "Pass3EventProposal.date_to is only valid when "
                "date_precision == 'range'."
            )
        if self.date_to is not None and self.date_to < self.date_from:
            raise ValueError(
                "Pass3EventProposal.date_to must be greater than or equal to date_from."
            )
        return self

    @model_validator(mode="after")
    def _check_participants_distinct(self) -> Self:
        """Reject duplicate participants — each participant id appears once."""
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("Pass3EventProposal.participant_ids must be unique.")
        return self


class Pass3Response(BaseModel):
    """Top-level Pass-3 response — a list of event proposals."""

    events: list[Pass3EventProposal]

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "DatePrecision",
    "EntityLabel",
    "EventCandidateReason",
    "EventDatePrecision",
    "EventType",
    "ExtractableEdgeType",
    "Pass1EntityProposal",
    "Pass1Response",
    "Pass2RelationshipProposal",
    "Pass2Response",
    "Pass3EventProposal",
    "Pass3Response",
]
