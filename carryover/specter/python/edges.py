"""Edge-side Pydantic surface: type whitelist, invariants, and read shape.

Four responsibilities live here so the edge writer (epic 05/06) and any
downstream consumer share one source of truth:

* :data:`EDGE_TYPES` enumerates the §8 relationship taxonomy as it stands
  after the Phase 2 reconciliation (see ADR-018): 23 types covering the
  original blueprint surface plus the geographic / publication /
  promotion edges added by epics 11 (extraction) and 13 (approve/reject
  workflow). The set is the *helper* allowlist — the public
  ``POST /relationships`` endpoint narrows it further by rejecting
  ``OPERATES_AS`` (which gets a dedicated endpoint in epic 06).
* :func:`validate_observation_for_edge` enforces the cross-field invariant
  that :class:`ObservationEntry` cannot enforce in isolation: a sourced edge
  needs ``source_document_id`` on every observation; an inferred edge needs
  a non-empty ``reasoning`` string.
* :data:`RELATIONSHIP_ENDPOINT_LABELS` and :func:`_validate_endpoint_labels`
  enforce per-type endpoint-label constraints (epic 06 / widened by epic
  12). ``MEMBER_OF`` constrains only the target side (any label →
  IdentityCluster); ``OPERATES_AS`` (Person → Identity) and
  ``EXTRACTED_FROM`` (QueueItem → Document) remain fully constrained. Other
  §8 types fall through (any label allowed) until a future epic asks for
  tighter rules.
* :func:`get_edge_confidence` and :class:`EdgeRead` carry the read-side
  projection. ``EdgeRead`` is deliberately *not* a subclass of
  :class:`api.models.base.EdgeBase` — the latter is an abstract write-side
  mixin requiring a class-level ``LABEL``, but a read projection across 18
  relationship types has no single label.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.models.observations import ObservationEntry

EDGE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "OPERATES_AS",
        "OWNS",
        "CONTROLS",
        "WORKS_FOR",
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
        "MENTIONS",
        "PUBLISHED_BY",
        "EXTRACTED_FROM",
        # Epic 13 — promotion edges written by the approve handlers.
        "PRIMARY_SUBJECT",
        "INVOLVED",
        "OCCURRED_AT",
        "REPORTED_BY",
    }
)


class EdgeTypeError(ValueError):
    """Raised when a candidate edge type is outside :data:`EDGE_TYPES`."""

    def __init__(self, edge_type: str) -> None:
        """Build a structured message naming the offending type.

        Args:
            edge_type: The candidate type that failed the allowlist check.
        """
        allowed = ", ".join(sorted(EDGE_TYPES))
        super().__init__(
            f"Edge type {edge_type!r} is not a §8 relationship type. "
            f"Allowed: {allowed}."
        )
        self.edge_type = edge_type


class ObservationInvariantError(ValueError):
    """Raised when an observation violates its edge's ``source_type`` invariant.

    Attributes:
        source_type: The parent edge's ``source_type``.
        missing_field: The observation field whose absence triggered the
            error (``"source_document_id"`` or ``"reasoning"``).
    """

    def __init__(
        self,
        source_type: Literal["sourced", "inferred"],
        missing_field: Literal["source_document_id", "reasoning"],
    ) -> None:
        """Build a structured message naming the offending field.

        Args:
            source_type: The parent edge's ``source_type``.
            missing_field: The observation field required by the invariant
                but absent on the supplied observation.
        """
        super().__init__(
            f"Edge with source_type={source_type!r} requires every "
            f"observation to populate {missing_field!r}."
        )
        self.source_type = source_type
        self.missing_field = missing_field


class EndpointLabelRule(NamedTuple):
    """Per-type constraint on the labels of an edge's endpoints.

    Attributes:
        expected_from: Label that must appear on the source node's label
            list, or ``None`` to allow any label.
        expected_to: Label that must appear on the target node's label
            list, or ``None`` to allow any label.
    """

    expected_from: str | None
    expected_to: str | None


RELATIONSHIP_ENDPOINT_LABELS: Final[dict[str, EndpointLabelRule]] = {
    # Epic 12 widened MEMBER_OF from Person-only to any label so
    # Organization and Identity candidates can also be cluster members.
    # See docs/epics/12-identity-clusters-plan.md §Decisions.
    "MEMBER_OF": EndpointLabelRule(None, "IdentityCluster"),
    "OPERATES_AS": EndpointLabelRule("Person", "Identity"),
    "EXTRACTED_FROM": EndpointLabelRule("QueueItem", "Document"),
    # Epic 13 — promotion edges (see docs/epics/13-approve-reject-workflow-plan.md).
    # PRIMARY_SUBJECT constrains only the source side (Document → any entity);
    # INVOLVED constrains only the target side (Person | Organization → Event)
    # to mirror the widened MEMBER_OF precedent.
    "PRIMARY_SUBJECT": EndpointLabelRule("Document", None),
    "INVOLVED": EndpointLabelRule(None, "Event"),
    "OCCURRED_AT": EndpointLabelRule("Event", "Location"),
    "REPORTED_BY": EndpointLabelRule("Event", "Document"),
    # MENTIONS mirrors PRIMARY_SUBJECT: Document → any entity. Emitted
    # by the entity-approve helper for non-primary mentions per
    # blueprint §11 step 8.
    "MENTIONS": EndpointLabelRule("Document", None),
    # DOCUMENTS is the inverse-direction sibling of PRIMARY_SUBJECT,
    # carried into the taxonomy in epic 11 (extractor schema).
    # Document → any entity for now; tighten after extractor calibration.
    "DOCUMENTS": EndpointLabelRule("Document", None),
}


class EdgeEndpointLabelError(ValueError):
    """Raised when an edge's endpoints carry the wrong Neo4j labels.

    Attributes:
        edge_type: §8 relationship type whose rule was violated.
        expected_from: Required label on the source node (``None`` if the
            rule does not constrain the source).
        expected_to: Required label on the target node (``None`` if the
            rule does not constrain the target).
        actual_from: The labels actually present on the source node.
        actual_to: The labels actually present on the target node.
    """

    def __init__(
        self,
        edge_type: str,
        expected_from: str | None,
        expected_to: str | None,
        actual_from: tuple[str, ...],
        actual_to: tuple[str, ...],
    ) -> None:
        """Build a structured message naming the offending endpoint labels."""
        super().__init__(
            f"Edge {edge_type!r} requires "
            f"from={expected_from!r} to={expected_to!r}; "
            f"got from={list(actual_from)!r} to={list(actual_to)!r}."
        )
        self.edge_type = edge_type
        self.expected_from = expected_from
        self.expected_to = expected_to
        self.actual_from = actual_from
        self.actual_to = actual_to


def _validate_endpoint_labels(
    edge_type: str,
    from_labels: Sequence[str],
    to_labels: Sequence[str],
) -> None:
    """Enforce :data:`RELATIONSHIP_ENDPOINT_LABELS` for ``edge_type``.

    Types without an entry fall through (any label allowed). The check is
    ``in`` rather than ``==`` so a node carrying multiple labels — e.g. a
    ``:Person:Indexed`` test fixture — still satisfies a rule that names
    one of them.

    Args:
        edge_type: §8 relationship type to look up in the rule table.
        from_labels: Labels present on the source node (e.g.
            ``("Person",)`` or ``("Person", "Indexed")``).
        to_labels: Labels present on the target node.

    Raises:
        EdgeEndpointLabelError: When a rule exists and one of the endpoints
            does not carry the required label.
    """
    rule = RELATIONSHIP_ENDPOINT_LABELS.get(edge_type)
    if rule is None:
        return
    actual_from = tuple(from_labels)
    actual_to = tuple(to_labels)
    from_ok = rule.expected_from is None or rule.expected_from in actual_from
    to_ok = rule.expected_to is None or rule.expected_to in actual_to
    if not from_ok or not to_ok:
        raise EdgeEndpointLabelError(
            edge_type,
            rule.expected_from,
            rule.expected_to,
            actual_from,
            actual_to,
        )


def _validate_edge_type(edge_type: str) -> None:
    """Raise :class:`EdgeTypeError` if ``edge_type`` is not in the §8 whitelist.

    Used by :func:`api.db.edge_ops._merge_edge_cypher` to close the
    relationship-type interpolation surface — Neo4j cannot parameterise the
    relationship type, so it must come from a closed allowlist.

    Args:
        edge_type: Candidate relationship type.

    Raises:
        EdgeTypeError: When ``edge_type`` is outside :data:`EDGE_TYPES`.
    """
    if edge_type not in EDGE_TYPES:
        raise EdgeTypeError(edge_type)


def validate_observation_for_edge(
    source_type: Literal["sourced", "inferred"],
    observation: ObservationEntry,
) -> None:
    """Enforce the per-observation invariant against the parent edge.

    A sourced edge must have ``source_document_id`` on every observation;
    an inferred edge must have a non-empty ``reasoning`` string. Whitespace
    only ``reasoning`` is rejected — a whitespace rationale is not a real
    rationale, mirroring the empty-q behaviour of the search route.

    Args:
        source_type: The parent edge's ``source_type``.
        observation: The single observation about to be appended.

    Raises:
        ObservationInvariantError: When the invariant is violated.
    """
    if source_type == "sourced":
        if observation.source_document_id is None:
            raise ObservationInvariantError("sourced", "source_document_id")
        return
    # source_type == "inferred"
    if observation.reasoning is None or not observation.reasoning.strip():
        raise ObservationInvariantError("inferred", "reasoning")


def get_edge_confidence(observations: Sequence[ObservationEntry]) -> float:
    """Return the §8 display confidence: ``max(o.confidence)`` over observations.

    Args:
        observations: Non-empty sequence of :class:`ObservationEntry`.

    Returns:
        The largest confidence value across ``observations``.

    Raises:
        ValueError: When ``observations`` is empty. An empty observation
            list is a broken edge invariant, never a low-confidence edge —
            so this raises rather than silently returning ``0.0``.
    """
    if not observations:
        raise ValueError("temporal_observations is empty; edge invariant broken.")
    return max(o.confidence for o in observations)


class GraphOverrideWarning(BaseModel):
    """Structured signal that the cross-graph rule downgraded an edge.

    Emitted only on POST responses where :func:`merge_edge` flipped the
    edge's effective ``graph`` from what the caller requested. Read paths
    (GET) leave the parent list empty.

    Attributes:
        requested_graph: The graph the caller asked for.
        effective_graph: The graph the edge was actually written to. Always
            ``"hypothesis"`` while the only override reason is the §5
            membership rule.
        reason: Discriminator for the override cause. Currently a single
            value (`endpoint_in_hypothesis`); future epics may add more.
        from_graph: The source node's ``graph`` at write time.
        to_graph: The target node's ``graph`` at write time.
    """

    requested_graph: Literal["knowledge", "hypothesis"]
    effective_graph: Literal["knowledge", "hypothesis"]
    reason: Literal["endpoint_in_hypothesis"]
    from_graph: Literal["knowledge", "hypothesis"]
    to_graph: Literal["knowledge", "hypothesis"]

    model_config = ConfigDict(extra="forbid")


class EdgeRead(BaseModel):
    """Read-side projection for any edge returned over the API.

    ``display_confidence`` is *required* on the wire but always recomputed
    by the model validator, so an incoming body cannot deceive readers and
    handlers do not need a separate compute step. Client-supplied values
    are overwritten.

    Attributes:
        id: Stable UUID identity for the edge.
        from_id: UUID of the source node.
        to_id: UUID of the target node.
        type: One of the §8 relationship types (see :data:`EDGE_TYPES`).
        graph: Partition selector (``"knowledge"`` or ``"hypothesis"``).
        source_type: Whether the edge is ``"sourced"`` or ``"inferred"``.
        created_at: Insertion timestamp (UTC).
        updated_at: Last-mutation timestamp (UTC).
        created_by: Operator who originated the edge.
        notes: Free-form analyst note.
        temporal_observations: Non-empty list of observations.
        display_confidence: ``max(o.confidence)`` across observations.
        graph_warnings: Override signals attached by POST handlers when
            the cross-graph rule downgraded the edge. Always empty on GET.
    """

    id: UUID
    from_id: UUID
    to_id: UUID
    type: str
    graph: Literal["knowledge", "hypothesis"]
    source_type: Literal["sourced", "inferred"]
    created_at: datetime
    updated_at: datetime
    created_by: Literal["hatim", "friend"]
    notes: str | None = Field(default=None, max_length=4_000)
    temporal_observations: list[ObservationEntry] = Field(min_length=1)
    display_confidence: float = Field(json_schema_extra={"readOnly": True})
    graph_warnings: list[GraphOverrideWarning] = Field(
        default_factory=list, json_schema_extra={"readOnly": True}
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _recompute_display_confidence(self) -> "EdgeRead":
        """Overwrite ``display_confidence`` from ``temporal_observations``.

        The field is required on the wire so OpenAPI documents it, but the
        only authoritative source is the observation list. Any client-
        supplied value is replaced after validation so readers never see a
        stale or hand-crafted confidence on an :class:`EdgeRead`.
        """
        computed = get_edge_confidence(self.temporal_observations)
        # ``validate_assignment`` is intentionally off on this model so the
        # assignment below cannot recurse into the validator.
        object.__setattr__(self, "display_confidence", computed)
        return self

    @model_validator(mode="after")
    def _check_edge_type(self) -> "EdgeRead":
        """Reject types outside :data:`EDGE_TYPES` at construction time."""
        _validate_edge_type(self.type)
        return self


__all__ = [
    "EDGE_TYPES",
    "RELATIONSHIP_ENDPOINT_LABELS",
    "EdgeEndpointLabelError",
    "EdgeRead",
    "EdgeTypeError",
    "EndpointLabelRule",
    "GraphOverrideWarning",
    "ObservationInvariantError",
    "_validate_edge_type",
    "_validate_endpoint_labels",
    "get_edge_confidence",
    "validate_observation_for_edge",
]
