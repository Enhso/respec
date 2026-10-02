# Contract fixtures

The contract between the Rust server and the Python worker: example worker inputs and progress messages that both test suites load (ISC-17). Fixtures use invented names only.

## `messages/`

Worker progress messages: JSON Lines on the worker's stdout, one object per line, told apart by a `kind` field. One message per file. Both suites load every file in the folder and fail if it is empty, so adding a file puts it under test on both sides.

| `kind` | Fields | Meaning |
|--------|--------|---------|
| `started` | `provider`, `model` | The worker is about to call the Model. |
| `done` | `provider`, `model`, `reply` | The call succeeded. |
| `failed` | `provider`, `reason`, `message` | The call failed. `message` is one plain sentence with a next step. |

- `provider` is `openrouter` or `gemini`, the same strings as `src/keys.rs`.
- `reason` is one of `auth`, `quota`, `rate_limit`, `model_unavailable`, `network`, `other`. There is a `failed_*.json` fixture for each.

The Rust side is `WorkerMessage` in `src/worker.rs`. The Python side is `respec_worker.messages`. Probe: `cargo test contract && uv run pytest -k contract`.
