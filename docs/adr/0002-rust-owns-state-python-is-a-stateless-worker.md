---
status: accepted
---

# Rust owns state; Python is a stateless worker

The Rust process owns the store, the domain invariants (Knowledge versus Hypothesis, promotion), the HTTP API, the job runner and serving the built UI. Python runs as one subprocess per job: it fetches the article, calls models, validates their output and reports progress as JSON Lines on stdout, and it never opens the store. This keeps the probabilistic edge (models, scraping) away from the invariants, and it is the pattern `~/projects/iw` already runs.

## Considered Options

- **PyO3 embedding**: couples the Python runtime to the server binary and its crashes to the server.
- **A long-running Python service**: a second stateful process to supervise on the operator's Mac.
- **Docker**: ruled out for the operator's machine.

## Consequences

The contract between the two lives in `contracts/fixtures/` and both test suites load it (ISC-17). The operator's Mac needs a Python runtime via uv as well as the Rust binary; pairing call 1 (PLAN S2) tests that install path in week one.
