"""Per-type relationship metadata consumed by graph-query helpers.

The constants in this module describe behaviour *about* the §8 edge
types rather than invariants enforced on the write path. They live in a
dedicated module (rather than next to :mod:`api.models.edges`) so the
edge-write surface stays focused on validation and IO, while
metadata-style facts (default traversal, future directionality hints,
display labels) gather here.

Today there is exactly one such fact:

* :data:`RELATIONSHIP_DEFAULT_TRAVERSE` — the boolean each §8 type carries
  to tell the Phase-3 ego-graph helper whether to follow it by default.
  Only ``OPERATES_AS`` is ``True``: from a Person, the cover-identity
  hop is *always* part of the ego graph.

No graph-query code lives here yet. The dict is a literal (not derived
from :data:`api.models.edges.EDGE_TYPES`) so adding a new §8 type forces
a deliberate decision about its default-traverse value; the completeness
test in :mod:`tests.models.test_edge_meta` is the safety net.
"""

from typing import Final

RELATIONSHIP_DEFAULT_TRAVERSE: Final[dict[str, bool]] = {
    "OPERATES_AS": True,
    "OWNS": False,
    "CONTROLS": False,
    "WORKS_FOR": False,
    "MEMBER_OF": False,
    "ASSOCIATED_WITH": False,
    "FAMILY_OF": False,
    "BORN_IN": False,
    "LOCATED_IN": False,
    "HEADQUARTERED_IN": False,
    "OPERATES_IN": False,
    "TRAVELED_TO": False,
    "PARTICIPATED_IN": False,
    "REGISTERED_TO": False,
    "FLAGGED_BY": False,
    "DOCUMENTS": False,
    "MENTIONS": False,
    "PUBLISHED_BY": False,
    "EXTRACTED_FROM": False,
    # Epic 13 — promotion edges. None of the promotion edges is part of
    # the default ego graph: they are reviewer-side provenance, surfaced
    # only when the ego helper opts in.
    "PRIMARY_SUBJECT": False,
    "INVOLVED": False,
    "OCCURRED_AT": False,
    "REPORTED_BY": False,
}


__all__ = ["RELATIONSHIP_DEFAULT_TRAVERSE"]
