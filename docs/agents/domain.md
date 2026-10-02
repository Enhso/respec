# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT-MAP.md`** at the repo root. It points at the shared glossary (`CONTEXT.md` at the root) and at any per-area glossary under `src/`, `python/` or `web/`. Always read the shared one, then each area glossary relevant to the topic.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in. Also check `<area>/docs/adr/` for area-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── CONTEXT-MAP.md
├── CONTEXT.md            <- shared domain language
├── docs/adr/             <- system-wide decisions
├── src/                  <- Rust server and store
│   ├── CONTEXT.md        (created when first needed)
│   └── docs/adr/         (created when first needed)
├── python/               <- Python worker, same pattern
└── web/                  <- web UI, same pattern
```

A term that crosses the contract in `contracts/fixtures/` belongs in the shared `CONTEXT.md`. An area glossary holds only terms that mean something in that area alone.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
