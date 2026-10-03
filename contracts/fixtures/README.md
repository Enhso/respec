# Contract fixtures

The contract between the Rust server and the Python worker: example worker inputs and progress messages that both test suites load (ISC-17). Fixtures use invented names only.

## `messages/`

Worker progress messages: JSON Lines on the worker's stdout, one object per line, told apart by a `kind` field. One message per file. Both suites load every file in the folder and fail if it is empty, so adding a file puts it under test on both sides.

| `kind` | Fields | Meaning |
|--------|--------|---------|
| `started` | `provider`, `model` | The worker is about to call the Model. |
| `progress` | `provider`, `stage`, `detail` | A test extraction is working. `detail` is one plain sentence. |
| `done` | `provider`, `model`, `reply` | The test call succeeded. |
| `entities` | `provider`, `model`, `document`, `entities` | The test extraction succeeded. `document` is `{url, title, chars}`, with `title` null when the page has none. Each entity is `{label, name, sentence}`, where `sentence` is the Proposal's first supporting sentence. |
| `failed` | `provider`, `reason`, `message` | The run failed. `message` is one plain sentence with a next step. |

- `provider` is `openrouter` or `gemini`, the same strings as `src/keys.rs`.
- `stage` is one of `fetching`, `calling_model`, `waiting_rate_limit`. There is a `progress_*.json` fixture for each.
- `reason` is one of `auth`, `quota`, `rate_limit`, `model_unavailable`, `network`, `fetch`, `bad_output`, `other`. There is a `failed_*.json` fixture for each. `fetch` means the article could not be fetched or had no body; `bad_output` means the Model's reply was not valid Pass 1 JSON.
- `done` and `entities` end a run successfully and `failed` ends it unsuccessfully. The server keeps the last of these as the outcome.

The Rust side is `WorkerMessage` in `src/worker.rs`. The Python side is `respec_worker.messages`. Probe: `cargo test contract && uv run pytest -k contract`.
