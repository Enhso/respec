"""Production ego-subgraph serializer for inference calls (ADR-022 winner).

Serializes an :class:`~api.models.io.EgoSubgraphResponse` as two Markdown
tables (nodes + edges) for use as the user message in a Claude inference call.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from api.models.io import EgoSubgraphResponse
from api.models.observations import ObservationEntry


def _collapse_observations(
    observations: list[ObservationEntry],
) -> dict[str, Any]:
    """Collapse a list of observations to a compact summary dict.

    Args:
        observations: Non-empty list of ObservationEntry instances.

    Returns:
        Dict with keys ``date_range`` (str), ``max_confidence`` (float),
        ``count`` (int).
    """
    min_from: date | None = None
    max_to: date | None = None
    max_confidence: float = 0.0

    for obs in observations:
        if obs.confidence > max_confidence:
            max_confidence = obs.confidence
        if obs.date_from is not None:
            if min_from is None or obs.date_from < min_from:
                min_from = obs.date_from
        if obs.date_to is not None:
            if max_to is None or obs.date_to > max_to:
                max_to = obs.date_to

    if min_from is None and max_to is None:
        date_range = "undated"
    elif max_to is None:
        date_range = f"{min_from} onwards"
    else:
        date_range = f"{min_from} to {max_to}"

    return {
        "date_range": date_range,
        "max_confidence": max_confidence,
        "count": len(observations),
    }


def serialize_ego_subgraph(response: EgoSubgraphResponse) -> str:
    """Serialize an ego subgraph as two Markdown tables (nodes + edges).

    The focal node's name is wrapped in ``**bold**``. Edge endpoints are
    resolved from UUIDs to display names via a lookup built from
    ``response.nodes``. Observations are collapsed to a single summary row
    per edge so long temporal histories don't dominate the context window.

    Args:
        response: The ego subgraph to serialize.

    Returns:
        A Markdown string with a ``## Nodes`` table and an ``## Edges`` table.
    """
    focal_id_str = str(response.focal_id)
    name_by_id: dict[str, str] = {str(n.id): n.name for n in response.nodes}

    lines: list[str] = ["## Nodes", ""]
    lines.append("| id | label | name | type | graph |")
    lines.append("|----|-------|------|------|-------|")
    for node in response.nodes:
        name = f"**{node.name}**" if str(node.id) == focal_id_str else node.name
        lines.append(
            f"| {node.id} | {node.label} | {name} | {node.type} | {node.graph} |"
        )

    lines += ["", "## Edges", ""]
    lines.append(
        "| from | to | rel_type | source_type | graph | confidence | date_range | observation_count |"  # noqa: E501
    )
    lines.append(
        "|------|----|----------|-------------|-------|------------|------------|-------------------|"
    )
    for edge in response.edges:
        collapsed = _collapse_observations(edge.temporal_observations)
        from_name = name_by_id.get(str(edge.from_id), str(edge.from_id))
        to_name = name_by_id.get(str(edge.to_id), str(edge.to_id))
        lines.append(
            f"| {from_name} | {to_name} | {edge.type} | {edge.source_type}"
            f" | {edge.graph} | {collapsed['max_confidence']:.2f}"
            f" | {collapsed['date_range']} | {collapsed['count']} |"
        )

    return "\n".join(lines)


__all__ = ["serialize_ego_subgraph"]
