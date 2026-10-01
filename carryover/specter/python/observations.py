"""Per-observation entry attached to every edge in the temporal graph.

Each :class:`ObservationEntry` records *when* and *with what confidence* an
edge was true, plus pointers to the originating evidence (a document URL or
an inference rationale). Edges aggregate one or more observations in their
``temporal_observations`` list; epic 05 enforces the
``sourced ⇒ source_document_id`` / ``inferred ⇒ reasoning`` invariant at
the edge boundary, since :class:`ObservationEntry` cannot inspect its parent
edge's ``source_type``.

Importing this module also resolves the forward reference inside
:class:`api.models.base.EdgeBase`, so any code that constructs an edge
subclass should ensure observations is imported first (commonly via
``import api.models``).
"""

from datetime import date, datetime
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api.models import base as _base
from api.models.base import _default_created_by, now_utc

_REASONING_MAX: Final[int] = 4_000
"""Cap on free-text ``reasoning`` on observations.

Mirrors the node-side ``_DESCRIPTION_MAX`` cap so edges and nodes share a
single long-text ceiling. Long-text edges cannot exceed what long-text
nodes can store — a 4_000-char rationale is generous-but-finite.
"""

DatePrecision = Literal["exact", "day", "month", "year", "range", "unknown"]
"""How tightly an observation's date interval is known.

Single source of truth for the two ``date_precision`` fields below —
promote-path parse helpers (``api.db.queue_item_promote_ops``) derive the
runtime membership set via ``typing.get_args(DatePrecision)`` instead of
duplicating the six strings a third time.
"""


class ObservationEntry(BaseModel):
    """A single time-bounded observation supporting a graph edge.

    Storage shape: ``merge_edge`` and the JSON codec both round-trip this
    class. The wire-input shape is :class:`ObservationInput` (no
    ``observed_by`` / ``observed_at`` — those are server-stamped). See
    ADR-011 for the wire-vs-server trust boundary that drives the split.

    Attributes:
        date_from: Earliest moment the edge was known to hold (UTC date).
        date_to: Latest moment the edge was known to hold (UTC date), or
            ``None`` if the relationship is open-ended.
        date_precision: How tightly the date interval is known.
        confidence: Analyst-assigned confidence in the unit interval.
        source_document_id: Pointer to a :class:`api.models.nodes.Document`
            when the observation came from primary evidence.
        reasoning: Free-form rationale used when the observation was
            inferred rather than sourced.
        observed_at: When the observation was recorded (UTC datetime).
        observed_by: Operator who recorded the observation.
    """

    date_from: date | None = None
    date_to: date | None = None
    date_precision: DatePrecision
    confidence: float = Field(ge=0, le=1)
    source_document_id: UUID | None = None
    reasoning: str | None = Field(default=None, max_length=_REASONING_MAX)
    observed_at: datetime = Field(default_factory=now_utc)
    observed_by: Literal["hatim", "friend"] = Field(default_factory=_default_created_by)

    model_config = ConfigDict(extra="forbid")


class ObservationInput(BaseModel):
    """Wire-input shape for an observation on an edge-write request.

    Carries the business fields of :class:`ObservationEntry` but *not*
    ``observed_by`` / ``observed_at``: both are server-stamped by
    :meth:`to_entry` from :func:`_default_created_by` and :func:`now_utc`.
    Forbidding both fields at the wire boundary (``extra="forbid"``) is
    the load-bearing audit-trail invariant codified in ADR-011 — a
    malformed body cannot forge attribution or back-date an observation.

    Attributes:
        date_from: Earliest moment the edge was known to hold (UTC date).
        date_to: Latest moment the edge was known to hold (UTC date), or
            ``None`` if the relationship is open-ended.
        date_precision: How tightly the date interval is known.
        confidence: Analyst-assigned confidence in the unit interval.
        source_document_id: Pointer to a Document when sourced.
        reasoning: Free-form rationale when inferred.
    """

    date_from: date | None = None
    date_to: date | None = None
    date_precision: DatePrecision
    confidence: float = Field(ge=0, le=1)
    source_document_id: UUID | None = None
    reasoning: str | None = Field(default=None, max_length=_REASONING_MAX)

    model_config = ConfigDict(extra="forbid")

    def to_entry(
        self,
        *,
        observed_by: Literal["hatim", "friend"] | None = None,
        observed_at: datetime | None = None,
    ) -> ObservationEntry:
        """Promote this wire shape to a fully-stamped :class:`ObservationEntry`.

        Production callers invoke with no arguments; both kwargs are
        accepted only so unit tests can pin deterministic stamping.
        ``observed_by`` falls back to :func:`_default_created_by` (the
        settings-derived process operator) and ``observed_at`` to
        :func:`now_utc`.

        Args:
            observed_by: Optional override for the operator field.
                Production callers pass ``None``.
            observed_at: Optional override for the timestamp field.
                Production callers pass ``None``.

        Returns:
            A :class:`ObservationEntry` populated from ``self`` plus the
            two server-stamped fields.
        """
        stamped_by = observed_by if observed_by is not None else _default_created_by()
        stamped_at = observed_at if observed_at is not None else now_utc()
        return ObservationEntry(
            date_from=self.date_from,
            date_to=self.date_to,
            date_precision=self.date_precision,
            confidence=self.confidence,
            source_document_id=self.source_document_id,
            reasoning=self.reasoning,
            observed_at=stamped_at,
            observed_by=stamped_by,
        )


# Resolve the forward reference inside ``EdgeBase`` now that ``ObservationEntry``
# is defined. ``model_rebuild`` is a no-op once successfully completed.
_base.EdgeBase.model_rebuild(_types_namespace={"ObservationEntry": ObservationEntry})
