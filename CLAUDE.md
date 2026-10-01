# Respec

Local-first OSINT graph: paste an article, review machine-proposed entities and relationships, keep the approved ones as a sourced Knowledge graph. Successor to `~/projects/specter`, frozen at commit `17a94aa` as reference.

`ISA.md` is the spec. Before building, read its Constraints and the Feature block you are working on. A claim closes only on the probe its Test Strategy row names, run red before the build and green after. Fold anything the work teaches back into the ISA (claims, Decisions, Learning) in the same change.

## The split

- **Rust owns state.** It is the only process that opens the mnestic store. It owns the domain invariants (Knowledge vs Hypothesis, promotion), the HTTP API, the job runner, and serving the built web UI from the same origin.
- **Python is stateless.** One worker subprocess per job fetches articles, calls models and validates their output. It reports progress as JSON Lines on stdout and never touches the store.
- The contract between them lives in `contracts/fixtures/`, loaded by both test suites.
- Plain append-only files in the data directory are the truth; the store is an index rebuilt from them.

## Carryover

`carryover/specter/` holds Specter code and prompts worth keeping. Port each file into Respec under a test, then delete it from `carryover/`, which must be empty by v1. The manifest is `carryover/specter/README.md`.

## Gotchas

- The mnestic crate's library name is `cozo` (`use cozo::...`). Pin it exactly: pre-1.0 releases break the storage format with no migration.
- Free models are shared and rate-limited. One long article can take minutes per pass and several 429s. Size the output-token budget from the model's spec, because Specter's fixed 4,096 cap silently truncated long articles.
- `corpus/bodies/` is gitignored third-party text, for local evals only.
- Model calls go through Respec's own small client over OpenAI-compatible endpoints. LiteLLM is out: prefix-routing bugs, and a compromised PyPI release in March 2026.
- Tests run against stores in temporary directories, never the operator's data directory.
- Fixtures and seed data use invented names. This is a public repo about real intelligence networks.

## Code rules

- Python: `uv` for everything, ruff and mypy strict clean, pytest, `orjson` for JSON, `logger.error` for errors. Docstrings on public functions.
- Rust: `cargo fmt`, `cargo clippy -- -D warnings` clean, `cargo test`.
- No emoji in code, docs or commits.
