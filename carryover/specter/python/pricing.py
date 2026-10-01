"""Per-pass confidence weighting for the three-pass extraction pipeline.

* :data:`PASS_CONFIDENCE_FACTOR` + :func:`weighted_confidence` — the §11
  step 5 invariant that "entities are more reliable than the relationships
  inferred between them, which are more reliable than the events composed
  over those relationships." The factor table is the one tunable knob the
  11.8 calibration runs revise; the final values are locked in ADR-013.
* :func:`total_input_tokens` — convenience sum of the three :class:`LLMUsage`
  input-token fields used by the cost row writer.
"""

from __future__ import annotations

from typing import Final, Literal

from api.llm import LLMUsage

PassNumber = Literal[1, 2, 3]


PASS_CONFIDENCE_FACTOR: Final[dict[PassNumber, float]] = {
    1: 1.0,
    2: 0.9,
    3: 0.8,
}
"""Per-pass discount applied to :attr:`SourceOutlet.credibility_score`.

The blueprint §11 step 5 calls these "pass-specific factors, TBD via
calibration." The defaults here are starting values for the 11.8
calibration runs and are locked (or revised) in
``docs/decisions/ADR-013-extraction-prompts.md``.

* Pass 1 (entities) — full fidelity: pulled directly from the article.
* Pass 2 (relationships) — mild discount: inferred between entities.
* Pass 3 (events) — larger discount: composite construct over multiple
  relationships.
"""

DEFAULT_UNATTRIBUTED_CREDIBILITY: Final[float] = 0.5
"""Fallback credibility for manually-ingested documents.

A :class:`api.models.nodes.Document` without an attached
``source_outlet_id`` (e.g. a manual paste of an unattributed leak) has
no :class:`SourceOutlet.credibility_score` to multiply against; the
orchestrator substitutes this midpoint value so the staged QueueItems
still carry a defensible confidence.
"""


def weighted_confidence(
    *,
    credibility_score: float,
    pass_number: PassNumber,
) -> float:
    """Return ``credibility_score * PASS_CONFIDENCE_FACTOR[pass_number]``.

    The product is clamped to the unit interval as a defensive measure
    — :attr:`SourceOutlet.credibility_score` is already bounded by its
    Pydantic ``Field(ge=0, le=1)``, but a future caller might pass a
    raw float; clamping closes that gap.

    Args:
        credibility_score: The source outlet's published credibility
            (or :data:`DEFAULT_UNATTRIBUTED_CREDIBILITY` for unattributed
            documents).
        pass_number: One of ``{1, 2, 3}``.

    Returns:
        The clamped product in ``[0.0, 1.0]``.
    """
    raw = credibility_score * PASS_CONFIDENCE_FACTOR[pass_number]
    return max(0.0, min(1.0, raw))


def total_input_tokens(usage: LLMUsage) -> int:
    """Return cached + uncached input tokens as one figure.

    Epic 10's :func:`api.cost.record_claude_call` records a single
    ``input_tokens`` value on the cost row; the three components —
    ``input_tokens``, ``cache_creation_input_tokens``,
    ``cache_read_input_tokens`` — sum here. The decomposition stays on
    the extraction audit row (which carries all three fields
    separately) so cache-hit ratios remain inspectable.

    Args:
        usage: Normalised usage block from :class:`~api.llm.LLMResult`.

    Returns:
        The sum of the three input-token fields.
    """
    return (
        usage.input_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens
    )


__all__ = [
    "DEFAULT_UNATTRIBUTED_CREDIBILITY",
    "PASS_CONFIDENCE_FACTOR",
    "PassNumber",
    "total_input_tokens",
    "weighted_confidence",
]
