"""Fuzzy-matching layer for the §11 Step 6 identity-cluster decision.

Four responsibilities live here so the extraction pipeline and any
future re-cluster path share one source of truth:

* :func:`score_candidates` — RapidFuzz ``token_set_ratio`` against a
  candidate pool; deterministic and side-effect-free.
* :func:`retrieve_cluster_candidates` — full-text-index lookup against
  ``entity_search``, label- and graph-filtered.
* :func:`classify_tier` — the §11 Step 6 ``merge / cluster / new``
  decision tree.
* :func:`cluster_for_entity` — orchestration entry point; the pipeline
  calls it once per Pass-1 entity.

The thresholds (:data:`MERGE_THRESHOLD`, :data:`CLUSTER_FLOOR`) and the
candidate-pool cap (:data:`MAX_CANDIDATES_PER_LOOKUP`) are constants so
the calibration run can land an override as a one-liner.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import UUID

from neo4j import AsyncDriver, AsyncManagedTransaction
from rapidfuzz import fuzz

from api.db._lucene import escape_lucene
from api.db.driver import async_session
from api.db.identity_cluster_ops import (
    ClusterMember,
    ClusterPersistenceError,
    create_identity_cluster_in_tx,
)
from api.models.nodes import IdentityCluster

# Annotation-only import — importing :mod:`api.extraction.schemas`
# eagerly would pull in :mod:`api.extraction.pipeline` through the
# package ``__init__`` and reintroduce a circular dependency.
if TYPE_CHECKING:
    from api.extraction.schemas import Pass1EntityProposal

# Blueprint §11 Step 6. Calibrated in story 12.6.
MERGE_THRESHOLD: Final[float] = 85.0
CLUSTER_FLOOR: Final[float] = 60.0
MAX_CANDIDATES_PER_LOOKUP: Final[int] = 25

CLUSTERABLE_LABELS: Final[frozenset[str]] = frozenset(
    {"Person", "Organization", "Identity"}
)
"""Labels the clustering layer retrieves candidates against.

``Vessel`` is keyed on ``registration`` (not name-fuzzy semantics);
``Location`` ambiguity wants a gazetteer; ``Event`` ambiguity is
handled by §7 promotion. The set is post-prototype-extensible.
"""

ClusterTier = Literal["merge", "cluster", "new"]
ClusterMemberLabel = Literal["Person", "Organization", "Identity"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ClusteringError(Exception):
    """Base class for clustering-policy failures.

    Subclasses signal control-flow concerns to the extraction pipeline
    (same style as :class:`api.cost.SpendCapExceeded`) rather than
    genuine programmer errors. The pipeline catches the base class and
    falls back to staging the QueueItem with ``tier="new"`` so a
    clustering failure cannot abort Pass 2/3.
    """


class CandidateRetrievalError(ClusteringError):
    """Raised when the full-text lookup fails (driver/Aura error).

    Attributes:
        name: The proposed entity name whose lookup failed.
    """

    def __init__(self, *, name: str, cause: BaseException) -> None:
        """Build a structured message naming the offending proposal."""
        super().__init__(
            f"Cluster-candidate retrieval failed for name={name!r}: {cause}"
        )
        self.name = name


# ---------------------------------------------------------------------------
# Scoring (story 12.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateEntity:
    """One Knowledge-graph entity returned by the full-text lookup.

    Mirrors :class:`api.models.io.SearchHit` minus the Lucene score; a
    separate type so the clustering layer stays decoupled from the
    HTTP-side search-response shape.

    Attributes:
        id: UUID of the candidate node.
        label: Top label on the candidate node (one of
            :data:`CLUSTERABLE_LABELS`).
        name: The candidate's ``name`` field.
        graph: Partition selector (always ``"knowledge"`` in v1 — see
            the §Decisions table in
            ``docs/epics/12-identity-clusters-plan.md``).
    """

    id: UUID
    label: str
    name: str
    graph: Literal["knowledge", "hypothesis"]


@dataclass(frozen=True, slots=True)
class CandidateScore:
    """One scored candidate: the entity plus its RapidFuzz score.

    Attributes:
        candidate: The :class:`CandidateEntity` that was scored.
        score: ``rapidfuzz.fuzz.token_set_ratio`` result in ``[0.0, 100.0]``.
    """

    candidate: CandidateEntity
    score: float


def score_candidates(
    name: str,
    candidates: list[CandidateEntity],
) -> list[CandidateScore]:
    """Score every candidate against ``name`` via ``token_set_ratio``.

    The deterministic tie-break (descending score, then ascending name)
    matters for the per-entity audit payload: calibration runs that
    re-run against the same corpus must produce byte-identical
    clustering decisions.

    Args:
        name: The proposed entity name (Pass-1
            :attr:`api.extraction.schemas.Pass1EntityProposal.name`).
        candidates: The retrieved candidate pool.

    Returns:
        Scored list in descending score order; ties broken by ascending
        candidate name.
    """
    scored = [
        CandidateScore(candidate=c, score=float(fuzz.token_set_ratio(name, c.name)))
        for c in candidates
    ]
    scored.sort(key=lambda s: (-s.score, s.candidate.name))
    return scored


# ---------------------------------------------------------------------------
# Candidate retrieval (story 12.2)
# ---------------------------------------------------------------------------


# The Cypher mirrors :data:`api.db.search._SEARCH_CYPHER` with two
# additions: a label whitelist (``WHERE any(l IN lbls WHERE l IN $labels)``)
# and the hard ``node.graph = $graph`` filter. The ``CASE`` over
# ``head(lbls)`` is structurally redundant for the v1 label set (all three
# clusterable labels key on ``node.name``) but kept so a future
# Vessel/Location addition can pick a different display field without
# rewriting the query.
_CLUSTER_CANDIDATE_CYPHER: Final[str] = (
    "CALL db.index.fulltext.queryNodes('entity_search', $q) YIELD node, score "
    "WITH node, score, labels(node) AS lbls "
    "WHERE any(l IN lbls WHERE l IN $labels) "
    "  AND node.graph = $graph "
    "RETURN "
    "  node.id AS id, "
    "  head(lbls) AS label, "
    "  CASE head(lbls) "
    "    WHEN 'Identity' THEN node.name "
    "    ELSE node.name "
    "  END AS name, "
    "  node.graph AS graph, "
    "  score "
    "ORDER BY score DESC "
    "LIMIT $limit"
)


async def retrieve_cluster_candidates(
    driver: AsyncDriver,
    *,
    name: str,
    label: str,
    limit: int = MAX_CANDIDATES_PER_LOOKUP,
) -> list[CandidateEntity]:
    """Return the top Knowledge-graph candidates for ``name``.

    The query is graph-filtered to ``knowledge`` (clusters never form
    against other Hypothesis proposals — see §Decisions in
    ``docs/epics/12-identity-clusters-plan.md``) and label-filtered to
    :data:`CLUSTERABLE_LABELS`. The proposal's own label is *not*
    required to match — a Person proposal can match an Identity
    (someone's alias) and vice versa; that's exactly the kind of
    ambiguity clustering exists to surface.

    Args:
        driver: Async Neo4j driver.
        name: Proposed entity name. Lucene-escaped inside; the caller
            passes raw text.
        label: The proposal's own label. Currently unused for filtering
            (accepted so a future per-label calibration can branch
            without a signature change).
        limit: Maximum candidates to return.

    Returns:
        Candidate entities in descending Lucene score order. Empty list
        when no candidate hits the index (a normal "new entity" case).

    Raises:
        CandidateRetrievalError: On any driver/Aura failure.
    """
    del label  # accepted for forward-compat; no current filter use.
    if not name.strip():
        return []
    q = escape_lucene(name)

    async def _tx(tx: AsyncManagedTransaction) -> list[CandidateEntity]:
        result = await tx.run(
            _CLUSTER_CANDIDATE_CYPHER,
            q=q,
            labels=list(CLUSTERABLE_LABELS),
            graph="knowledge",
            limit=limit,
        )
        candidates: list[CandidateEntity] = []
        async for record in result:
            candidates.append(
                CandidateEntity(
                    id=UUID(record["id"]),
                    label=record["label"],
                    name=record["name"],
                    graph=record["graph"],
                )
            )
        return candidates

    try:
        async with async_session(driver) as session:
            return await session.execute_read(_tx)
    except Exception as exc:
        raise CandidateRetrievalError(name=name, cause=exc) from exc


# ---------------------------------------------------------------------------
# Tier classification (story 12.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusterDecision:
    """Outcome of one :func:`classify_tier` call.

    Attributes:
        tier: ``"merge"`` / ``"cluster"`` / ``"new"`` discriminator.
        top_score: Highest RapidFuzz score across all candidates.
            ``0.0`` when no candidate was retrieved (then
            ``tier == "new"``).
        threshold_used: The :data:`MERGE_THRESHOLD` value applied
            (per-run override or default). Recorded for audit
            reproducibility.
        scored_matches: Candidates with ``score >= CLUSTER_FLOOR`` in
            descending score order. Empty when ``tier == "new"``.
    """

    tier: ClusterTier
    top_score: float
    threshold_used: float
    scored_matches: list[CandidateScore] = field(default_factory=list)


def classify_tier(
    scores: list[CandidateScore],
    *,
    merge_threshold: float = MERGE_THRESHOLD,
    cluster_floor: float = CLUSTER_FLOOR,
) -> ClusterDecision:
    """Apply the §11 Step 6 tier rule to a list of scored candidates.

    Decision tree (top score wins):
        * ``top_score >= merge_threshold`` → ``merge``
        * ``cluster_floor <= top_score < merge_threshold`` → ``cluster``
        * ``top_score < cluster_floor`` (or no candidates) → ``new``

    The ``cluster``-tier ``scored_matches`` list also includes any
    *other* candidates above ``cluster_floor`` so the IdentityCluster
    can be formed with the full ambiguous pool, not just the top
    match. The ``merge``-tier ``scored_matches`` is the same pool —
    the reviewer needs to see the runner-up scores to confirm the top
    match is the right merge target.

    Args:
        scores: The output of :func:`score_candidates` (already sorted).
        merge_threshold: Boundary between ``cluster`` and ``merge``
            tiers. Overridable per ingestion run.
        cluster_floor: Boundary between ``new`` and ``cluster`` tiers.
            Hard policy floor (not overridable in v1).

    Returns:
        A :class:`ClusterDecision`.

    Raises:
        ValueError: When ``cluster_floor >= merge_threshold``.
    """
    if cluster_floor >= merge_threshold:
        raise ValueError(
            f"cluster_floor ({cluster_floor}) must be strictly less "
            f"than merge_threshold ({merge_threshold})."
        )
    if not scores:
        return ClusterDecision(
            tier="new",
            top_score=0.0,
            threshold_used=merge_threshold,
        )
    top = scores[0].score
    above_floor = [s for s in scores if s.score >= cluster_floor]
    if top >= merge_threshold:
        return ClusterDecision(
            tier="merge",
            top_score=top,
            threshold_used=merge_threshold,
            scored_matches=above_floor,
        )
    if top >= cluster_floor:
        return ClusterDecision(
            tier="cluster",
            top_score=top,
            threshold_used=merge_threshold,
            scored_matches=above_floor,
        )
    return ClusterDecision(
        tier="new",
        top_score=top,
        threshold_used=merge_threshold,
    )


# ---------------------------------------------------------------------------
# Orchestration (story 12.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusteringOutcome:
    """Per-entity clustering result returned to the pipeline.

    Attributes:
        tier: ``"merge"`` / ``"cluster"`` / ``"new"``.
        threshold_used: The applied :data:`MERGE_THRESHOLD`.
        cluster_id: Set when ``tier == "cluster"``; the freshly-created
            :class:`api.models.nodes.IdentityCluster` id. ``None``
            otherwise.
        scored_matches: Top-N matches above :data:`CLUSTER_FLOOR`
            (empty on ``"new"``).
    """

    tier: ClusterTier
    threshold_used: float
    cluster_id: UUID | None
    scored_matches: list[CandidateScore]


def _dedupe_preserving_order(names: Iterable[str]) -> list[str]:
    """Return ``names`` with case-insensitive duplicates removed.

    Preserves the first-seen casing of each canonical form. Used to
    seed :attr:`api.models.nodes.IdentityCluster.candidate_names` so
    the proposal's name appears first and matched candidate names
    follow without overlapping it.
    """
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


async def cluster_for_entity(
    driver: AsyncDriver,
    *,
    proposal: Pass1EntityProposal,
    source_document_id: UUID,
    created_by: Literal["hatim", "friend"],
    merge_threshold: float = MERGE_THRESHOLD,
) -> ClusteringOutcome:
    """End-to-end clustering for one Pass-1 entity proposal.

    Pipeline:
        retrieve candidates → score → classify_tier
            * tier == "merge"   → return outcome (no cluster created)
            * tier == "cluster" → build cluster + members → persist via
              :func:`api.db.identity_cluster_ops.create_identity_cluster_in_tx`
            * tier == "new"     → return outcome (no cluster, no merge)

    Cluster construction (``tier == "cluster"``):
        * ``candidate_names`` = ``[proposal.name, *match_names]`` after
          case-insensitive deduplication, order-preserving.
        * ``evidence`` = ``list(proposal.supporting_sentences)``.
        * ``confidence`` = ``decision.top_score / 100.0``.
        * ``fuzzy_threshold`` = ``merge_threshold``.

    Args:
        driver: Async Neo4j driver.
        proposal: One :class:`Pass1EntityProposal`.
        source_document_id: The Document the proposal came from.
        created_by: Operator identity for the cluster + MEMBER_OF
            observations.
        merge_threshold: The run-time threshold (default
            :data:`MERGE_THRESHOLD`).

    Returns:
        A :class:`ClusteringOutcome`.

    Raises:
        CandidateRetrievalError: On Aura failure during retrieval.
        ClusterPersistenceError: On rollback during cluster persistence.
    """
    candidates = await retrieve_cluster_candidates(
        driver,
        name=proposal.name,
        label=proposal.label,
    )
    scores = score_candidates(proposal.name, candidates)
    decision = classify_tier(scores, merge_threshold=merge_threshold)

    if decision.tier != "cluster":
        return ClusteringOutcome(
            tier=decision.tier,
            threshold_used=decision.threshold_used,
            cluster_id=None,
            scored_matches=decision.scored_matches,
        )

    # tier == "cluster": persist a fresh IdentityCluster and one
    # MEMBER_OF edge per matched candidate (filtered to the labels the
    # widened endpoint rule accepts — see api/models/edges.py).
    members: list[ClusterMember] = []
    for s in decision.scored_matches:
        if s.candidate.label in ("Person", "Organization", "Identity"):
            members.append(
                ClusterMember(
                    id=s.candidate.id,
                    label=s.candidate.label,  # type: ignore[arg-type]
                )
            )
    instance = IdentityCluster(
        candidate_names=_dedupe_preserving_order(
            [proposal.name, *(s.candidate.name for s in decision.scored_matches)]
        ),
        evidence=list(proposal.supporting_sentences),
        confidence=decision.top_score / 100.0,
        fuzzy_threshold=merge_threshold,
        created_by=created_by,
    )

    async def _tx(tx: AsyncManagedTransaction) -> IdentityCluster:
        return await create_identity_cluster_in_tx(
            tx,
            instance=instance,
            members=members,
            source_document_id=source_document_id,
            observed_by=created_by,
        )

    try:
        async with async_session(driver) as session:
            written = await session.execute_write(_tx)
    except ClusterPersistenceError:
        raise
    except Exception as exc:
        raise ClusterPersistenceError(
            cluster_id=instance.id,
            reason=f"unexpected error: {exc}",
        ) from exc

    return ClusteringOutcome(
        tier="cluster",
        threshold_used=decision.threshold_used,
        cluster_id=written.id,
        scored_matches=decision.scored_matches,
    )


def serialize_outcome_matches(outcome: ClusteringOutcome) -> list[dict[str, Any]]:
    """Render ``outcome.scored_matches`` as the QueueItem ``fuzzy.matches`` list.

    The shape (id, label, name, graph, score) is consumed by the
    review-queue UI (epic 13 / 21).

    Args:
        outcome: A :class:`ClusteringOutcome`.

    Returns:
        One dict per match, descending score order.
    """
    return [
        {
            "candidate_id": str(s.candidate.id),
            "label": s.candidate.label,
            "name": s.candidate.name,
            "graph": s.candidate.graph,
            "score": s.score,
        }
        for s in outcome.scored_matches
    ]


__all__ = [
    "CLUSTERABLE_LABELS",
    "CLUSTER_FLOOR",
    "MAX_CANDIDATES_PER_LOOKUP",
    "MERGE_THRESHOLD",
    "CandidateEntity",
    "CandidateRetrievalError",
    "CandidateScore",
    "ClusterDecision",
    "ClusterTier",
    "ClusteringError",
    "ClusteringOutcome",
    "classify_tier",
    "cluster_for_entity",
    "retrieve_cluster_candidates",
    "score_candidates",
    "serialize_outcome_matches",
]
