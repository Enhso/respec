"""Concrete Pydantic node models for every Neo4j label in blueprint §9.

The :data:`NODE_MODELS` registry is the single source of truth for label
enumeration. :func:`api.db.constraints.init_constraints` consumes it to
generate one uniqueness constraint per label, and the full-text index DDL
is hand-tuned in that module to mirror the same set.
"""

from datetime import date, datetime
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import Field, HttpUrl, StringConstraints, field_validator, model_validator

from api.models.actionlog import ActionLog
from api.models.base import NodeBase

_SHA256_PATTERN = r"^[a-f0-9]{64}$"

# Length caps for free-text string fields. ``_DESCRIPTION_MAX`` is sized to
# hold a fetched article body pre-truncated by
# :data:`api.settings.Settings.body_text_max_chars` (epic 14); it bounds the
# wire-side surface so a pathological body cannot blow past Neo4j's
# per-property storage limits or the autonomous-extractor's prompt budget.
_NAME_MAX: Final[int] = 200
_TITLE_MAX: Final[int] = 500
_DESCRIPTION_MAX: Final[int] = 50_000

_ALIAS_CONSTRAINT = StringConstraints(min_length=1, max_length=_NAME_MAX)

# Shared annotations for the LLM-summary fields added inline to each of the
# nine entity models (not NodeBase, not a mixin) so QueueItem/ActionLog never
# see them.
_SummaryText = Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)] | None
_SummaryModelName = Annotated[str, StringConstraints(max_length=_NAME_MAX)] | None


class Person(NodeBase):
    """Natural person. Tracks aliases and biographical metadata."""

    LABEL = "Person"

    name: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    aliases: list[Annotated[str, _ALIAS_CONSTRAINT]] = Field(default_factory=list)
    nationality: Annotated[str, StringConstraints(max_length=_NAME_MAX)] | None = None
    dob: date | None = None
    description: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    photo_url: HttpUrl | None = None
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class Organization(NodeBase):
    """Group entity (intel agency, corporation, NGO, shell company, ...)."""

    LABEL = "Organization"

    name: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    aliases: list[Annotated[str, _ALIAS_CONSTRAINT]] = Field(default_factory=list)
    type: Literal["intel", "military", "corporate", "ngo", "media", "shell", "other"]
    country: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    description: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    active: bool
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class Identity(NodeBase):
    """Cover identity, alias, or pseudonym attributable to a Person."""

    LABEL = "Identity"

    name: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    person_id: UUID | None = None
    used_from: date | None = None
    used_until: date | None = None
    context: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class Vessel(NodeBase):
    """Conveyance: aircraft, ship, helicopter, ..."""

    LABEL = "Vessel"

    registration: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    type: Literal["aircraft", "ship", "helicopter", "other"]
    operator_id: UUID | None = None
    description: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class Location(NodeBase):
    """Geographic location at varying levels of precision."""

    LABEL = "Location"

    name: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    country: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    coordinates: tuple[float, float] | None = None
    type: Literal["city", "facility", "address", "coordinates"]
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class Event(NodeBase):
    """A meeting, transaction, incident, or other point-in-time occurrence.

    Per blueprint §7, events live in either the ``knowledge`` or ``hypothesis``
    graph; no default is provided so the caller must specify deliberately.

    The model carries a cross-field invariant: ``date_to`` is required
    exactly when ``date_precision == "range"`` and must not precede
    ``date_from`` when present. The check runs on every full-model
    construction (POST through the factory, the bespoke PATCH's
    re-validation pass, and any future seed/ingest call site).
    """

    LABEL = "Event"

    graph: Literal["knowledge", "hypothesis"]
    title: Annotated[str, StringConstraints(max_length=_TITLE_MAX)]
    type: Literal[
        "meeting",
        "operation",
        "transaction",
        "appointment",
        "incident",
        "other",
    ]
    date_from: date
    date_to: date | None = None
    date_precision: Literal["exact", "day", "month", "year", "range"]
    location_id: UUID | None = None
    description: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None

    @model_validator(mode="after")
    def _check_date_consistency(self) -> Self:
        """Enforce ``date_to`` / ``date_precision`` / ``date_from`` invariants."""
        is_range = self.date_precision == "range"
        if is_range and self.date_to is None:
            raise ValueError(
                "Event.date_to is required when date_precision == 'range' "
                "(date_from / date_to / date_precision must agree)."
            )
        if not is_range and self.date_to is not None:
            raise ValueError(
                "Event.date_to is only valid when date_precision == 'range' "
                "(date_from / date_to / date_precision must agree)."
            )
        if self.date_to is not None and self.date_to < self.date_from:
            raise ValueError(
                "Event.date_to must be greater than or equal to date_from."
            )
        return self


class Document(NodeBase):
    """Source document referenced by edge observations.

    ``last_extraction_*`` fields are a denormalised current-state stamp
    written by :func:`api.extraction.pipeline.extract_document` as the
    final act of every run (ADR-027) — a single writer, best-effort (a
    stamp failure logs but never fails the pipeline). They are not a
    second audit trail: the ``ClaudeExtractionRun`` ActionLog rows remain
    the deep history. All five read as ``None`` — "never extracted" — for
    a Document no run has touched, and for one where the run aborted on
    :class:`api.cost.SpendCapExceeded` before the stamp (a policy
    interruption, not a failure).

    ``last_extraction_outcome`` semantics: ``"success"`` — every
    attempted pass parsed; ``"partial"`` — at least one pass succeeded
    before a later pass failed; ``"failed"`` — Pass 1 itself died.
    ``last_extraction_error`` carries the first non-success, non-skipped
    pass's error detail (or its outcome label as a fallback cause),
    truncated to 200 chars; ``None`` on a fully successful run.
    """

    LABEL = "Document"

    title: Annotated[str, StringConstraints(max_length=_TITLE_MAX)]
    sub_type: Literal[
        "article",
        "court_filing",
        "invoice",
        "leaked_doc",
        "call_record",
        "academic_paper",
        "other",
    ]
    date: date
    url: HttpUrl | None = None
    content_hash: Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]
    source_outlet_id: UUID | None = None
    language: Literal["en", "ru", "other"]
    description: Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]
    last_extraction_at: datetime | None = None
    last_extraction_model: str | None = None
    last_extraction_outcome: Literal["success", "partial", "failed"] | None = None
    last_extraction_staged: int | None = None
    last_extraction_error: str | None = None
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class SourceOutlet(NodeBase):
    """Publisher / outlet feeding documents into the graph."""

    LABEL = "SourceOutlet"

    name: Annotated[str, StringConstraints(max_length=_NAME_MAX)]
    url: HttpUrl
    tier: Literal[1, 2, 3]
    credibility_score: float = Field(ge=0, le=1)
    rss_url: HttpUrl | None = None
    last_fetched: datetime | None = None
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None


class IdentityCluster(NodeBase):
    """Hypothesis-graph cluster of candidate Person/Identity matches.

    ``graph`` is fixed to ``"hypothesis"`` for this label: clusters are by
    definition unconfirmed. PATCH already strips ``graph`` via
    :data:`api.models.io._UPDATE_BANNED_FIELDS`, so the validator below
    only guards the CREATE path (a malicious or mistaken
    ``{"graph": "knowledge", ...}`` wire body returns 422 before any
    Cypher runs).
    """

    LABEL = "IdentityCluster"

    graph: Literal["knowledge", "hypothesis"] = "hypothesis"
    candidate_names: list[Annotated[str, _ALIAS_CONSTRAINT]] = Field(min_length=1)
    evidence: list[Annotated[str, StringConstraints(max_length=_DESCRIPTION_MAX)]] = (
        Field(default_factory=list)
    )
    confidence: float = Field(ge=0, le=1)
    status: Literal["open", "resolved", "rejected"] = "open"
    fuzzy_threshold: float = Field(ge=0, le=100, default=85)
    summary: _SummaryText = None
    summary_generated_at: datetime | None = None
    summary_model: _SummaryModelName = None

    @field_validator("graph")
    @classmethod
    def _enforce_hypothesis_graph(cls, value: str) -> str:
        if value != "hypothesis":
            raise ValueError(
                "IdentityCluster.graph must be 'hypothesis'; clusters are "
                "unconfirmed by definition."
            )
        return value


class QueueItem(NodeBase):
    """Provisional Phase 1 representation of an extractor review item.

    Full schema lands in epic 09; the minimal shape here is enough to declare
    the label and uniqueness constraint without committing to fields whose
    semantics are still in flux.
    """

    LABEL = "QueueItem"

    graph: Literal["knowledge", "hypothesis"] = "hypothesis"
    document_id: UUID | None = None
    kind: Literal["entity", "relationship", "event"]
    status: Literal["open", "approved", "rejected", "edited", "needs_review"] = "open"
    payload: dict[str, Any] = Field(default_factory=dict)
    cluster_hint_id: UUID | None = None


NODE_MODELS: tuple[type[NodeBase], ...] = (
    Person,
    Organization,
    Identity,
    Vessel,
    Location,
    Event,
    Document,
    SourceOutlet,
    IdentityCluster,
    QueueItem,
    ActionLog,
)
