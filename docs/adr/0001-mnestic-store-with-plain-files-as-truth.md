---
status: accepted
---

# mnestic store, with plain files as the truth

Respec stores its graph in mnestic, an embedded store opened only by the Rust process and pinned to an exact version. Because mnestic is pre-1.0 and its releases break the storage format with no migration, append-only files in the data directory (document text, proposals, review decisions) are the source of truth and the store is an index rebuilt from them. `~/projects/iw` already runs mnestic, so its store layout and know-how carry over.

## Considered Options

- **Neo4j Aura** (Specter's store): roughly 7 to 8k of Specter's 20k backend lines were workarounds for a shared cloud database.
- **Kuzu**: archived in October 2025.
- **Plain SQLite**: viable and stable. The 2026-10-01 rejection cited lost as-of history and search fusion. A first-principles pass on 2026-10-02 found that no v1 claim uses either, and that Specter's whole corpus produced about 150 entities, so SQLite as the truth would drop the rebuild layer (ISC-5, 50, 51). The principal kept mnestic. SQLite stays the fallback if mnestic breaks.

## Consequences

The rebuild path is load-bearing: rebuild parity (ISC-5), torn-line tolerance (ISC-50) and a schema-marker rebuild at startup (ISC-51) exist because of this choice. A throwaway store prototype settles the schema before the store module (PLAN S5).
