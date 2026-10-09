# Contract fixtures

The contract between the Rust server and the Python worker: example worker inputs and progress messages that both test suites load (ISC-17). Fixtures use invented names only.

## `messages/`

Worker progress messages: JSON Lines on the worker's stdout, one object per line, told apart by a `kind` field. One message per file. Both suites load every file in the folder and fail if it is empty, so adding a file puts it under test on both sides.

| `kind` | Fields | Meaning |
|--------|--------|---------|
| `started` | `provider`, `model` | The worker is about to call the Model. |
| `progress` | `provider`, `stage`, `detail`, `pass_number`, `pass_count` | A run is working. `detail` is one plain sentence. `pass_number` and `pass_count` say "pass n of N" while a pass of `extract` runs, and are null otherwise. |
| `done` | `provider`, `model`, `reply` | The test call succeeded. |
| `entities` | `provider`, `model`, `document`, `entities`, `dropped` | The entity Proposals of a Pass 1 reading. `document` is `{url, title, chars}`, with `title` null when the page has none and `url` null when the Document text came from a file. Each entity is `{id, label, name, sentence, sentence_start, sentence_end, date, place}`: `id` is the short id the worker stamped (`e1`, `e2`, ...), `sentence` is the Proposal's first supporting sentence that was found in the Document text (see "Sentences and offsets"), and `date` and `place` belong to an Event (`date` is ISO 8601 and may stop at the year or the month, and its form is its precision) and are null for every other kind. `dropped` counts the Proposals left out: items of the Model's reply that were malformed, and those with no supporting sentence found in the Document text. `sentences_dropped` counts the supporting sentences not found in it. |
| `relationships` | `provider`, `model`, `relationships`, `dropped` | The relationship Proposals of an `extract` run's Pass 2. Each is `{type, from_id, to_id, date_from, date_to, date_precision, sentence, sentence_start, sentence_end}`, where `from_id` and `to_id` are entity ids from the `entities` message and `PARTICIPATED_IN` links a participant to the Event it took part in. `dropped` counts the items of the Model's reply left out: malformed ones, ones with no supporting sentence found in the Document text, ones naming an id that was never given, and `PARTICIPATED_IN` links that do not end at an Event or that start at one. `sentences_dropped` counts the supporting sentences not found in the Document text. |
| `failed` | `provider`, `reason`, `message` | The run failed. `message` is one plain sentence with a next step. |

- `provider` is `openrouter` or `gemini`, the same strings as `src/keys.rs`.
- `stage` is one of `fetching`, `calling_model`, `waiting_rate_limit`. There is a `progress_*.json` fixture for each.
- `reason` is one of `auth`, `quota`, `rate_limit`, `model_unavailable`, `network`, `fetch`, `bad_output`, `output_truncated`, `other`. There is a `failed_*.json` fixture for each. `fetch` means the article could not be fetched or read, or had no body; `bad_output` means the Model's reply was not valid Pass 1 or Pass 2 JSON; `output_truncated` means the reply stopped at its output-token limit (`finish_reason` `length`), so it was never parsed.
- `done`, `entities` and `relationships` end a run successfully and `failed` ends it unsuccessfully. The server keeps the last of these as the outcome. A `test-extraction` run ends with `entities`. An `extract` run sends `entities` after Pass 1 and ends with `relationships`, or with `failed` if Pass 2 goes wrong.

### Sentences and offsets

Before a Proposal is emitted, each of its supporting sentences is looked up in the Document text. Curly and straight quotes (single and double) count as equal, and a run of whitespace counts as one space; nothing else may differ. A sentence that is not found is dropped, and a Proposal left with no sentence is dropped. A sentence that occurs more than once is taken at its first occurrence.

`sentence` is the slice of the Document text itself, not the Model's wording of it, and `sentence_start` and `sentence_end` locate it there. **Offsets count Unicode code points, not bytes or UTF-16 units, and `sentence_end` is exclusive**, so `text[sentence_start:sentence_end] == sentence` with Python indexing. Rust and the web UI convert from code points to their own units. The Document text is the text the worker read: for a `--text-file` the file's own characters, with line ends as they are in the file. `document.chars` is its length in code points.

The Python contract test checks each fixture's offsets span exactly its sentence (`end - start` is the sentence's length in code points), and the Rust one checks the same with `chars().count()`.

The Rust side is `WorkerMessage` in `src/worker.rs`. The Python side is `respec_worker.messages`. Probe: `cargo test contract && uv run pytest -k contract`.
