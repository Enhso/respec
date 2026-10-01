"""Pydantic models for Claude inference responses and resolved candidates."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Closed set of relationship types the inference prompt allows Claude to propose.
# Mirrors the "Proposable relationship types" section in prompts/system.md.
PROPOSABLE_TYPES: frozenset[str] = frozenset(
    {
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
        "INVOLVED",
        "OCCURRED_AT",
    }
)


class InferredRelationship(BaseModel):
    """One proposed relationship from Claude's inference response.

    Attributes:
        from_name: Name of the source entity as it appears in the subgraph.
        to_name: Name of the target entity as it appears in the subgraph.
        type: Relationship type from the proposable-types allowlist.
        confidence: Calibrated confidence score between 0.0 and 1.0.
        reasoning: 2–3 sentence justification citing subgraph evidence.
    """

    from_name: str
    to_name: str
    type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=20)

    @field_validator("type")
    @classmethod
    def type_must_be_proposable(cls, v: str) -> str:
        """Reject types outside the proposable-types allowlist.

        Args:
            v: The relationship type string to validate.

        Returns:
            The type unchanged if it is in the allowlist.

        Raises:
            ValueError: If ``v`` is not in :data:`PROPOSABLE_TYPES`.
        """
        if v not in PROPOSABLE_TYPES:
            allowed = ", ".join(sorted(PROPOSABLE_TYPES))
            raise ValueError(f"'{v}' is not a proposable type. Allowed: {allowed}")
        return v


class InferenceResponse(BaseModel):
    """Wrapper for Claude's ``{{"proposals": [...]}}`` response envelope.

    Attributes:
        proposals: List of proposed relationships. Empty list is valid.
    """

    proposals: list[InferredRelationship] = Field(default_factory=list)


class ResolvedCandidate(BaseModel):
    """An inferred relationship with entity UUIDs looked up from the subgraph.

    Attributes:
        from_name: Source entity name as proposed by Claude.
        to_name: Target entity name as proposed by Claude.
        from_id: UUID of the source entity, or ``None`` if not found.
        to_id: UUID of the target entity, or ``None`` if not found.
        type: Relationship type.
        confidence: Confidence score.
        reasoning: Justification text.
        resolved: ``True`` when both ``from_id`` and ``to_id`` were found.
        warnings: List of resolution warning strings (empty when resolved).
    """

    from_name: str
    to_name: str
    from_id: UUID | None
    to_id: UUID | None
    type: str
    confidence: float
    reasoning: str
    resolved: bool
    warnings: list[str] = Field(default_factory=list)


class PersistedCandidate(BaseModel):
    """Result of persisting one resolved candidate to the graph.

    Attributes:
        from_name: Source entity name.
        to_name: Target entity name.
        type: Relationship type.
        confidence: Confidence score.
        resolved: Whether both endpoints were resolved.
        edge_id: UUID of the written Hypothesis edge (only when resolved).
        queue_item_id: UUID of the written QueueItem.
        warnings: Propagated from the resolution step.
    """

    from_name: str
    to_name: str
    type: str
    confidence: float
    resolved: bool
    edge_id: UUID | None = None
    queue_item_id: UUID | None = None
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "InferenceResponse",
    "InferredRelationship",
    "PersistedCandidate",
    "PROPOSABLE_TYPES",
    "ResolvedCandidate",
]
