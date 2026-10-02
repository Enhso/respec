# Context map

Respec has one shared domain language and three code areas. Read the shared glossary first, then the glossary of the area you are working in, if it has one.

| Context | Glossary |
|---|---|
| Shared domain | `CONTEXT.md` |
| Rust server and store | `src/CONTEXT.md` (none yet) |
| Python worker | `python/CONTEXT.md` (none yet) |
| Web UI | `web/CONTEXT.md` (none yet) |

A term that crosses the contract in `contracts/fixtures/` stays in the shared glossary. Create an area glossary the first time a term means something in that area alone, and update its row here. System-wide ADRs live in `docs/adr/`; area-scoped ones go in `<area>/docs/adr/`.
