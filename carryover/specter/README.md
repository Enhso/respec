Material carried over from ~/projects/specter at commit 17a94aa. Port, don't import: each file is reference material to be rewritten into Respec under test, then deleted from here.

| path in carryover | original path | what it is |
|---|---|---|
| prompts/extraction/system.md | api/extraction/prompts/system.md | Three-pass extraction system prompt with output contract and schemas |
| prompts/extraction/pass1_entities.md | api/extraction/prompts/pass1_entities.md | Pass 1 user prompt template: entity extraction from article body |
| prompts/extraction/pass2_relationships.md | api/extraction/prompts/pass2_relationships.md | Pass 2 user prompt template: dyadic relationships between Pass 1 entities |
| prompts/extraction/pass3_events.md | api/extraction/prompts/pass3_events.md | Pass 3 user prompt template: aggregate flagged relationships into events |
| prompts/inference/system.md | api/inference/prompts/system.md | System prompt for proposing inferred edges over an ego subgraph |
| prompts/inference/summary_system.md | api/inference/prompts/summary_system.md | System prompt for writing a prose brief on a focal entity |
| python/client.py | api/extraction/client.py | LiteLLM wrapper issuing cached-system extraction pass calls |
| python/pricing.py | api/extraction/pricing.py | Per-pass confidence weighting and token-usage helpers for extraction |
| python/fetch.py | api/ingest/fetch.py | Async HTML fetch with bs4 then trafilatura body/title/date extraction |
| python/canonicalize.py | api/ingest/canonicalize.py | URL canonicalisation rules for content-hash dedup keying |
| python/clustering.py | api/clustering.py | RapidFuzz fuzzy matching and identity-cluster decision layer |
| python/nodes.py | api/models/nodes.py | Pydantic node models per Neo4j label, with NODE_MODELS registry |
| python/edges.py | api/models/edges.py | Pydantic edge models: 23-type whitelist, invariants, read shape |
| python/edge_meta.py | api/models/edge_meta.py | Per-edge-type metadata constants for graph-query helpers |
| python/observations.py | api/models/observations.py | Per-edge temporal ObservationEntry model with confidence and evidence |
| python/serializer.py | api/inference/serializer.py | Ego-subgraph to Markdown-table serializer for inference prompts |
| python/extraction_schemas.py | api/extraction/schemas.py | Pydantic contract for the three extraction passes' LLM output |
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
| fixtures/articles/bellingcat_supply.html | tests/ingest/fixtures/articles/bellingcat_supply.html | Bellingcat-style article HTML fixture for fetch tests |
| fixtures/articles/empty_body.html | tests/ingest/fixtures/articles/empty_body.html | Stub HTML with only nav, no article body |
| fixtures/articles/insider_795.html | tests/ingest/fixtures/articles/insider_795.html | The Insider-style article HTML fixture for fetch tests |
| fixtures/articles/minimal_no_selector.html | tests/ingest/fixtures/articles/minimal_no_selector.html | Short article HTML matching no structural selector; trafilatura fallback |
