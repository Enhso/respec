Material carried over from ~/projects/specter at commit 17a94aa. Port, don't import: each file is reference material to be rewritten into Respec under test, then deleted from here.

| path in carryover | original path | what it is |
|---|---|---|
| prompts/inference/system.md | api/inference/prompts/system.md | System prompt for proposing inferred edges over an ego subgraph |
| prompts/inference/summary_system.md | api/inference/prompts/summary_system.md | System prompt for writing a prose brief on a focal entity |
| python/pricing.py | api/extraction/pricing.py | Per-pass confidence weighting and token-usage helpers for extraction |
| python/canonicalize.py | api/ingest/canonicalize.py | URL canonicalisation rules for content-hash dedup keying |
| python/clustering.py | api/clustering.py | RapidFuzz fuzzy matching and identity-cluster decision layer |
| python/nodes.py | api/models/nodes.py | Pydantic node models per Neo4j label, with NODE_MODELS registry |
| python/edges.py | api/models/edges.py | Pydantic edge models: 23-type whitelist, invariants, read shape |
| python/edge_meta.py | api/models/edge_meta.py | Per-edge-type metadata constants for graph-query helpers |
| python/observations.py | api/models/observations.py | Per-edge temporal ObservationEntry model with confidence and evidence |
| python/serializer.py | api/inference/serializer.py | Ego-subgraph to Markdown-table serializer for inference prompts |
| python/inference_schemas.py | api/inference/schemas.py | Pydantic contract for inferred-relationship LLM output |
| web/tokens.ts | web/src/theme/tokens.ts | Design tokens for knowledge/hypothesis and sourced/inferred signals |
| web/variables.css | web/src/theme/variables.css | CSS custom properties for graph and edge colours |
| web/GraphBadge.tsx | web/src/theme/GraphBadge.tsx | Badge component for knowledge vs hypothesis graph kind |
| web/SourceBadge.tsx | web/src/theme/SourceBadge.tsx | Badge component for sourced vs inferred edge kind |
| web/canvasStyles.ts | web/src/app/graph/canvasStyles.ts | Cytoscape stylesheet for the ego graph canvas |
| web/timelineModel.ts | web/src/app/profile/timelineModel.ts | Pure model building profile timeline items from events, spans, documents |
| web/computeDigest.ts | web/src/app/profile/computeDigest.ts | Pure function computing the profile digest from relationships |
| web/groupByDocument.ts | web/src/app/queue/groupByDocument.ts | Groups queue items by document_id |
| web/EgoGraphCanvas.tsx | web/src/app/graph/EgoGraphCanvas.tsx | Cytoscape ego graph component; defines pure buildElements |

Ported and deleted from this folder:

| was | now | what happened |
|---|---|---|
| prompts/extraction/pass2_relationships.md | `python/src/respec_worker/prompts/pass2_relationships.md` | Rewritten: entities are listed by short id, and the event-promotion instruction is gone |
| prompts/extraction/pass3_events.md | nowhere | Dropped with Pass 3: Events are Entities extracted in Pass 1 (ADR 0004) |
| python/extraction_schemas.py | `python/src/respec_worker/extraction.py` | Pass 1 (S2 issue 03) and Pass 2 ported. Dropped: Pass 3, `event_candidate` and its reason, server-stamped uuids (the worker stamps `e1`, `e2`, ...). Added: the Event fields (date, place), partial dates, per-item validation (a malformed item is dropped and counted, not a failed reply) |
| python/client.py | `python/src/respec_worker/extraction.py`, `client.py` | The JSON envelope scan and the parse failure (S2 issue 03). `call_pass` and its LiteLLM, prompt-caching and fallback chain are dropped for Respec's own client |
