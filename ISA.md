---
task: "Respec: rebuild Specter local-first and prove the loop"
slug: 20261001-respec
project: respec
phase: climbing
progress: 13/67
started: 2026-10-01T19:12:36Z
updated: 2026-10-07T00:00:00Z
principal_stated_goal: "we'll build a new fork of it keeping only essential and reusable items (if any) and start from scratch"
principal_stated_goal_source: conversation
principal_stated_goal_signal: 4
principal_stated_goal_locked: 2026-10-01T19:12:36Z
context_sufficient: true
interview_invoked: true
---

## Problem

A researcher tracking Russian state-adjacent networks reads long investigations from outlets like The Insider and Bellingcat. The connections live in his head and in flat notes, so recall fades: he remembers a name but not where he read it, or misses that a new article's "Ivanov" is the man from a story six months ago.

Specter, the first attempt (`~/projects/specter`, frozen at `17a94aa`), never ran end to end. Its extraction failed on all 29 recorded runs, the friend was never set up on it, and most of its code and docs worked around a shared cloud Neo4j database, Docker and a dev proxy. Respec starts over and keeps only the parts that held up.

## Vision

The friend opens Respec from a bookmark on his Mac, pastes a link to a new Insider investigation, and goes to make tea. When he comes back the article has been read and a queue is waiting: people, organisations and links, each with the sentence it came from. He approves most, fixes one name, rejects a guess. One proposal says "possible match: Viktor Arlenko, from the March shipping story", and he merges it. Arlenko's profile now shows the new link, the ego graph draws it, and one click opens the sentence in the article. He never touches a terminal after install.

Euphoric surprise is that match: Respec remembered what he forgot.

## Out of Scope

- **No shared data.** Each operator runs his own instance. No sync, no shared database, no accounts, no auth.
- **No automatic ingestion.** No RSS poller, no scheduler. The operator pastes a URL or the article text.
- **No LLM inference.** Hypothesis generation over ego graphs returns only after extraction is measured and trusted.
- **No identity-cluster view.** v1 offers "merge into existing" or "create new" at review time; split and cluster management come later.
- **No summaries, digests or run-compare.** The network is the product.
- **No second-order ego expansion and no shared side panel across graph views.** Cut on 2026-10-01; revisit after the trial.
- **No paid models, cost tracking or spend caps.** Free models only.
- **No Russian-language sources, Telegram or X.**
- **No Windows or Linux packaging.** Development on Linux continues; the shipped target is macOS.
- **No Docker, cloud hosting, public surface, or code signing.**

## Language

The glossary lives in `CONTEXT.md`, and the hard-to-reverse decisions in `docs/adr/`. This ISA keeps the dated Decisions log.

## Principles

- **Prove the loop before polishing it.** A feature lands only on a pipeline that has already run live on real articles.
- **The firewall is the product.** Nothing enters Knowledge without an operator's approval, and no view blurs the line.
- **No provenance, no fact.** Every approved relationship points to a document and a verbatim sentence in it.
- **Plain files are the truth; the database is an index.** If the store breaks or its format changes, rebuild it from the files.
- **Rust owns state and invariants. Python handles the probabilistic edge and keeps no state.**
- **Design bug classes away instead of filing them.** One origin instead of a dev proxy, throwaway stores instead of shared test data.
- **Free and slow is the default.** Design for minutes-long, rate-limited model calls.
- **The ISA and git are the record.** Process serves the product.

## Constraints

- No Neo4j or any server database. Storage is mnestic, embedded, pinned to an exact version, and opened only by the Rust process.
- Append-only files under the data directory (document text, proposals, review decisions) are the source of truth. The mnestic store must be rebuildable from them alone.
- Rust (stable) for the server; Python 3.12 with uv for the worker.
- The worker runs as one subprocess per job, emits JSON Lines on stdout, keeps no state, and never opens the store. No PyO3, no long-running Python service.
- The web UI (React, Vite, Cytoscape) is built to static files and served by the Rust binary from the same origin as the API. The shipped app has no dev proxy.
- macOS on Apple Silicon is the operator target (the friend: M1 Pro, macOS 27.0.1), so the build target is `aarch64-apple-darwin`. No Docker. Install follows written instructions, with no command-line work after that.
- Free models by default, at least two providers. Keys are entered in the UI, stored locally outside the repo, and never logged.
- Binds to 127.0.0.1 only, and rejects foreign Host and Origin headers. Loopback alone does not stop DNS rebinding or cross-site requests from pages the operator visits.
- Model calls go through one small HTTP client over the providers' OpenAI-compatible chat endpoints, with a fixed provider-to-host table. No LiteLLM.
- Builds are locked: `Cargo.lock` and `uv.lock` are committed, and every build and install uses `--locked` or `--frozen`.
- Tests never touch real operator data. CI runs from the first code commit.
- Python: ruff, mypy strict, pytest. Rust: cargo fmt, clippy with `-D warnings`, cargo test.

## Goal

"we'll build a new fork of it keeping only essential and reusable items (if any) and start from scratch"

Respec is a local web app, one instance per operator on his own Mac. It turns a pasted article into a review queue of proposed entities and relationships, each with its source sentence, and turns approved items into a searchable Knowledge graph where every link cites its documents. v1 is done when two things hold:

- Five real articles extract end to end on free models (F1), and a real-sized extraction works on the friend's own keys from his Mac (ISC-45).
- After one guided install, the friend ingests and reviews an article we did not pre-stage, unsupervised, within a week (ISC-40).

Derived anchors used in Test Strategy:

- `prove-the-loop`: the pipeline runs live before features stack on it.
- `firewall`: Knowledge only through approval, always sourced.
- `friend-usable`: no terminal after install.
- `local-store`: mnestic plus plain-file truth.

## Not yet specified

- fog: whether long articles need chunking within the two passes. Resolved by the F7 evals, which run on the worker alone.
- fog: the mnestic schema. Open: Knowledge and Hypothesis as separate relations or a status column, and how valid time and transaction time map onto observations. Resolved by a throwaway store prototype before F2.
- fog: where model specs come from (each provider's model-list API, or a curated file) and which providers beyond OpenRouter and Gemini. Candidates: OpenRouter's models API and Gemini's models.list. Settled in the worker session.
- fog: how a breaking mnestic upgrade is applied (replay from files, or export and import). Designed alongside ISC-5.
- fog: what a rejection means when a later Document proposes the same Relationship: shown again, shown as previously rejected, or suppressed. Settled in the review session (PLAN S8).

## Features

### F0 · Cross-cutting
Why: the gates and data rules that let every later slice be trusted, set up before the first feature instead of retrofitted in month three.

- [x] ISC-1: CI runs the Rust gates (fmt, clippy `-D warnings`, test) on every push.
- [x] ISC-2: CI runs the Python gates (ruff, mypy strict, pytest) on every push.
- [x] ISC-3: The mnestic dependency is pinned to an exact version in `Cargo.toml`.
- [x] ISC-4: Anti: no test reads or writes outside a temporary directory.
- [ ] ISC-5: After approve, edit, reject and merge fixtures, deleting the store and running `respec rebuild` reproduces a Knowledge graph with the same content hash, transaction times excluded. (after: ISC-26)
- [x] ISC-6: Anti: the server listens on 127.0.0.1 only.
- [ ] ISC-7: Anti: no provider key appears in the repo, the data directory's logs, or job output.
- [ ] ISC-8: `carryover/` is gone by v1. Every item in it was ported under test or dropped with a Decisions row.
- [x] ISC-46: Anti: a request whose Host header is not Respec's loopback address and port is rejected.
- [x] ISC-47: Anti: a state-changing request whose Origin is not Respec's own is rejected.
- [x] ISC-48: CI builds with `--locked` (Rust) and `--frozen` (Python) against committed lock files.
- [ ] ISC-49: A second Respec instance on the same data directory refuses to start.
- [ ] ISC-50: A torn last line in an append-only file, left by a crash mid-write, is skipped with a warning and startup continues.
- [ ] ISC-51: When the store's schema marker differs from the binary's, Respec rebuilds the store from the files at startup.

### F1 · Walking skeleton
Why: one pasted article travels the whole pipeline on a free model before anything else is built, which is the step Specter never achieved.

- [ ] ISC-9: `POST /api/documents` with a URL returns a job id and saves the fetched article text under the data directory.
- [ ] ISC-10: Submitting raw pasted text creates a document the same way, and requires a source URL or title plus a publication date.
- [ ] ISC-11: Every article in the five-article smoke set (one is over 20k characters) yields at least one relationship proposal whose sentence is verified in the document text, through the running app on a free model. (after: ISC-9)
- [ ] ISC-12: Anti: a model reply cut off by the output-token cap is reported as `output_truncated`, never as a parse error.
- [ ] ISC-13: The output-token budget for each call is the model's max-output spec, capped at its context window minus the prompt.
- [ ] ISC-14: A per-minute 429 is retried with backoff, and the job shows "waiting on rate limit" instead of failing.
- [ ] ISC-14.1: A per-day quota 429 pauses the job with a plain message naming the provider and when to retry.
- [ ] ISC-15: Killing the worker mid-job and restarting resumes the job from the last completed pass.
- [ ] ISC-16: The UI shows each job's state (queued, fetching, pass n of N, done, failed with reason) without a page reload.
- [ ] ISC-17: The Rust and Python test suites both validate the same contract fixtures in `contracts/fixtures/`.
- [ ] ISC-52: When the selected model still fails after retries, the job falls through an ordered list of fallback models across providers.
- [ ] ISC-53: Every failure class (auth, daily quota, rate limit, model gone, truncated, network) maps to a plain-language message with a next step.
- [x] ISC-54: Anti: no request to Gemini carries prompt-caching directives (Specter's cached-content calls hit a zero quota).
- [ ] ISC-55: Jobs run one at a time, in submission order.
- [x] ISC-61: Extraction makes two passes per article: pass 1 proposes entities, Events included (with date and place), and pass 2 proposes relationships, including participants to Events. There is no third pass.
- [x] ISC-63: A Model reply with one malformed item still yields its other items; the malformed item is dropped and counted, never a failure of the whole reply.

### F2 · Review and the firewall
Why: nothing reaches Knowledge without a deliberate approval, and every approved fact leads back to its sentence.

- [ ] ISC-18: The review queue groups proposals by document, and each shows its verbatim supporting sentence.
- [ ] ISC-19: Approving a proposal writes it to Knowledge with an observation citing the document and sentence.
- [ ] ISC-20: Rejecting a proposal records the rejection in the decision log and removes it from the queue.
- [ ] ISC-21: Editing a proposal before approval stores the edited version and keeps the original in the log.
- [ ] ISC-22: Anti: only the approval path can write to Knowledge, merges included (the store's Knowledge write takes an approval value; one call site).
- [ ] ISC-23: `respec check` passes. It confirms every Knowledge observation's sentence appears in its document text at its stored offsets.
- [ ] ISC-23.1: The same integrity check runs at startup, and the UI shows a banner when it finds a violation.
- [ ] ISC-24: Knowledge and Hypothesis carry two redundant visual signals in every view: colour plus glyph for nodes, colour plus line style for edges.
- [ ] ISC-56: Anti: a Knowledge relationship never has a Hypothesis endpoint.
- [ ] ISC-57: Approving a relationship that already exists in Knowledge adds an observation to it, never a second relationship.
- [ ] ISC-58: An entity proposal whose sentence lacks the entity's name as written in the text is flagged in the queue.

### F3 · Entity matching
Why: "this Ivanov is the man from the March story" is the moment the product exists for.

- [ ] ISC-25: Each entity proposal lists the closest existing Knowledge entities, ranked by match score.
- [ ] ISC-26: The operator can merge a proposal into a candidate, and the proposal's name becomes an alias of it.
- [ ] ISC-27: The match threshold is calibrated on labelled pairs of entities that recur across corpus articles, including transliteration variants and look-alikes that must not merge. Precision and recall go in Decisions. (after: ISC-42)

### F4 · Navigation
Why: the friend finds any entity in seconds and follows the network outward from it.

- [ ] ISC-28: Search returns an entity by name or alias.
- [ ] ISC-29: An entity profile lists relationships grouped by type, each with its observations and source documents.
- [ ] ISC-30: The ego graph shows an entity's first-order neighbours in the fcose layout.
- [ ] ISC-31: [DROPPED — see Decisions 2026-10-01]
- [ ] ISC-32: A source link opens the stored document text with the cited sentence highlighted.

### F5 · Providers and models
Why: the friend picks a working free model himself, without editing a file, and can see what he is choosing.

- [ ] ISC-33: Keys entered on the settings page for OpenRouter and Google Gemini are saved in the operator's config directory, outside the repo.
- [ ] ISC-34: For each provider with a valid key, settings lists its available models with context window, max output, free or paid, and a short description.
- [ ] ISC-35: Switching the model in settings applies to the next job with no restart.
- [ ] ISC-36: A "test this model" button makes one small live call and shows success or a plain-language failure reason.
- [x] ISC-37: Anti: a key is only ever sent to its own provider's API host.
- [x] ISC-59: Anti: the API never returns a stored key; settings show only its last four characters.

### F6 · Mac install and handoff
Why: the friend installs once from written instructions and never needs a terminal again.

- [ ] ISC-45: In week one, on the friend's Mac and with his own keys, a test extraction of a 20k-character article succeeds.
- [ ] ISC-38: A fresh macOS user account with no Rust or Python gets a running Respec by following the install instructions. Only a real Mac closes this; a CI runner ships with Rust and Python and skips Gatekeeper.
- [ ] ISC-39: After a reboot, Respec answers at the friend's bookmark without him running any command.
- [ ] ISC-40: The friend ingests and reviews an article we did not pre-stage, unsupervised, within a week of the pairing session. (after: ISC-11, ISC-38)
- [ ] ISC-41: Upgrading to a new Respec version keeps all of the operator's data.
- [ ] ISC-60: A diagnostics button downloads a zip of logs and job records that contains no keys.

### F7 · Extraction quality
Why: measure quality on real articles before trusting or tuning prompts, the calibration Specter never ran.

- [ ] ISC-42: A labelled eval set of at least 10 corpus articles, at least 4 of them over 15k characters, lists each article's key entities, key events and key relationships.
- [ ] ISC-43: Recall of labelled key entities is at least 0.8 on the eval set with the chosen free model. (after: ISC-42)
- [ ] ISC-43.1: Recall of labelled key relationships is at least 0.6 on the eval set (provisional, re-set at the quality gate). (after: ISC-42)
- [ ] ISC-62: Recall of labelled key events is at least 0.6 on the eval set. Below that, Events are dropped or a third pass returns, with a Decisions row either way. (after: ISC-42)
- [ ] ISC-44: Anti: a proposal whose sentence is not found in the document text, under one normalisation rule for quotes and whitespace, is dropped and counted, never staged.
- [ ] ISC-44.1: Every staged sentence is stored with its character offsets in the document text.

## Test Strategy

| isc | type | check | threshold | tool | anchors_to |
|-----|------|-------|-----------|------|------------|
| ISC-1 | curl | CI workflow has fmt, clippy, test jobs and the latest run is green | success | `curl -s api.github.com/repos/Enhso/respec/actions/runs?per_page=1` | derived: prove-the-loop |
| ISC-2 | curl | CI workflow has ruff, mypy, pytest jobs and the latest run is green | success | `curl -s api.github.com/repos/Enhso/respec/actions/runs?per_page=1` | derived: prove-the-loop |
| ISC-3 | bash | mnestic requirement uses `=` exact pin | match | `grep -E 'mnestic = "=' Cargo.toml` | derived: local-store |
| ISC-4 | bash | full test run with HOME set to an empty temp dir leaves it empty | 0 files | `scripts/test-isolation.sh` | derived: local-store |
| ISC-5 | bash | after approve/edit/reject/merge fixtures, rebuild yields the same content hash (tx time excluded) | identical | `scripts/rebuild-parity.sh` | derived: local-store |
| ISC-6 | bash | listening sockets for the respec process are loopback only | 127.0.0.1 only | `lsof -nP -iTCP -sTCP:LISTEN -a -c respec` | literal |
| ISC-7 | bash | key patterns absent from repo, data-dir logs, job output after a live run | 0 hits | `scripts/key-leak-scan.sh` | literal |
| ISC-8 | bash | carryover directory does not exist | absent | `test ! -e carryover` | literal |
| ISC-46 | curl | request with Host: evil.example is rejected | 403 | `curl -i -H 'Host: evil.example' localhost:PORT/api/health` | derived: firewall |
| ISC-47 | curl | POST with Origin: https://evil.example is rejected | 403 | `curl -i -X POST -H 'Origin: https://evil.example' localhost:PORT/api/settings/keys` | derived: firewall |
| ISC-48 | bash | CI workflow uses --locked and --frozen; lock files tracked | present | `grep -E -- '--locked|--frozen' .github/workflows/*.yml` | derived: local-store |
| ISC-49 | bash | start a second instance on the same data dir | exits non-zero | `cargo test second_instance_refused` | derived: local-store |
| ISC-50 | bash | append a half-written line to a log file; startup warns and continues | starts | `cargo test torn_line_tolerated` | derived: local-store |
| ISC-51 | bash | bump the schema marker; startup rebuilds and counts match | rebuilt | `cargo test schema_marker_rebuild` | derived: local-store |
| ISC-9 | curl | POST a corpus URL returns 202 with job id; text file exists | 202 + file | `curl -i -X POST localhost:PORT/api/documents` | derived: prove-the-loop |
| ISC-10 | curl | POST text without date is 422; with URL or title and date is 202 and listed | 422 / 202 | `curl -i -X POST localhost:PORT/api/documents` | derived: firewall |
| ISC-11 | bash | live smoke: each of 5 articles has at least one relationship proposal with a verified sentence | 5/5 | `scripts/smoke-live.sh` | derived: prove-the-loop |
| ISC-12 | bash | worker test feeds a length-truncated fixture reply | `output_truncated` | `uv run pytest -k truncated` | derived: prove-the-loop |
| ISC-13 | bash | worker test: max_tokens = min(model max output, context window - prompt tokens) | equal | `uv run pytest -k output_budget` | derived: prove-the-loop |
| ISC-14 | bash | worker test: mocked per-minute 429 then 200 succeeds and emits a rate-limit progress message | pass | `uv run pytest -k rate_limit` | derived: prove-the-loop |
| ISC-14.1 | bash | worker test: mocked per-day quota 429 emits a paused progress message with provider and retry time | pass | `uv run pytest -k daily_quota` | derived: friend-usable |
| ISC-15 | bash | integration test kills worker after pass 1; resumed job skips pass 1 | pass | `cargo test resume_after_kill` | derived: prove-the-loop |
| ISC-16 | screenshot | job card shows each state transition live | all states seen | Interceptor | derived: friend-usable |
| ISC-17 | bash | both suites load every file in `contracts/fixtures/` | pass | `cargo test contract && uv run pytest -k contract` | derived: prove-the-loop |
| ISC-52 | bash | worker test: primary fails after retries; next model in the list succeeds | pass | `uv run pytest -k fallback` | derived: friend-usable |
| ISC-53 | bash | worker test: each failure class maps to a message with a next step | all classes | `uv run pytest -k failure_messages` | derived: friend-usable |
| ISC-54 | bash | worker test: Gemini request bodies carry no cache directives | 0 | `uv run pytest -k gemini_no_cache` | derived: prove-the-loop |
| ISC-55 | bash | submit three jobs; at most one worker process runs at a time | <= 1 | `cargo test serial_queue` | derived: prove-the-loop |
| ISC-61 | bash | worker test on a fixture: exactly two model calls; Event entities carry date and place and have participant relationships | 2 calls | `uv run pytest -k two_pass_events` | derived: prove-the-loop |
| ISC-63 | bash | worker test: a reply with one garbled item key yields the rest, with a dropped count of 1, for both passes | pass | `uv run pytest -k malformed_item` | derived: prove-the-loop |
| ISC-18 | screenshot | queue grouped by document with sentences visible | visible | Interceptor | derived: firewall |
| ISC-19 | curl | approve then fetch entity: observation has document id and sentence | present | `curl -i localhost:PORT/api/...` | derived: firewall |
| ISC-20 | curl | reject then queue omits item; log has rejection | pass | `curl -i` + log grep | derived: firewall |
| ISC-21 | curl | edit then approve; log holds original and edit | both present | `curl -i` + log grep | derived: firewall |
| ISC-22 | bash | Knowledge write fn requires approval type; one caller | 1 call site | `cargo test` + `grep -rn` | derived: firewall |
| ISC-23 | bash | integrity check over the store | 0 violations | `respec check` | derived: firewall |
| ISC-23.1 | screenshot | corrupt one observation in a temp data dir; startup shows the banner | banner visible | Interceptor | derived: firewall |
| ISC-24 | screenshot | K/H nodes and edges in profile, queue and ego graph | 2 signals each | Interceptor | derived: firewall |
| ISC-56 | bash | store test: writing a Knowledge relationship with a Hypothesis endpoint fails | rejected | `cargo test knowledge_endpoints` | derived: firewall |
| ISC-57 | curl | approve the same relationship from two documents; one relationship, two observations | 1 / 2 | `curl -i` + store query | derived: firewall |
| ISC-58 | bash | staging test: entity sentence missing the surface name is flagged | flagged | `cargo test entity_sentence_flag` | derived: firewall |
| ISC-25 | screenshot | proposal for an entity that recurs across two corpus articles shows the existing entity as top candidate | top-1 | Interceptor | derived: friend-usable |
| ISC-26 | curl | merge proposal into entity; entity aliases include proposal name | present | `curl -i` | derived: firewall |
| ISC-27 | bash | calibration over recurring-entity, transliteration and look-alike pairs prints precision and recall | recorded | `uv run python -m respec_worker.calibrate` | derived: friend-usable |
| ISC-28 | curl | search for a known alias on the full corpus returns its entity | top-1 | `curl -s localhost:PORT/api/search?q=` | derived: friend-usable |
| ISC-29 | screenshot | profile of a recurring corpus entity shows grouped relationships with sources | visible | Interceptor | derived: friend-usable |
| ISC-30 | screenshot | ego graph of a recurring entity shows first-order neighbours in fcose layout | visible | Interceptor | derived: friend-usable |
| ISC-32 | screenshot | source link opens text with sentence highlighted | highlighted | Interceptor | derived: firewall |
| ISC-33 | bash | keys saved via settings land in the config dir, not the repo | outside repo | `scripts/key-location.sh` | derived: friend-usable |
| ISC-34 | screenshot | model list with four spec fields per model for each provider | 4 fields | Interceptor | derived: friend-usable |
| ISC-35 | curl | switch model; next job's progress messages name the new model | new model | `curl -i` | derived: friend-usable |
| ISC-36 | screenshot | test button success and failure messages | both shown | Interceptor | derived: friend-usable |
| ISC-37 | bash | unit test of provider-to-host mapping for every provider | exact hosts | `uv run pytest -k provider_hosts` | literal |
| ISC-59 | curl | GET settings returns only the last four characters of each key | masked | `curl -s localhost:PORT/api/settings` | literal |
| ISC-45 | manual | pairing call 1: his keys, his Mac, test extraction of a 20k-char article | success | principal on encounter | derived: friend-usable |
| ISC-38 | manual | fresh macOS account on a real Mac, install by following the instructions | running | principal on encounter | derived: friend-usable |
| ISC-39 | manual | reboot, open bookmark, app responds | responds | principal on encounter | derived: friend-usable |
| ISC-40 | manual | friend reports an unstaged article reviewed, unsupervised | done | principal on encounter | derived: friend-usable |
| ISC-41 | bash | install vN, add data, upgrade to vN+1, counts unchanged | identical | `scripts/upgrade-parity.sh` | derived: local-store |
| ISC-60 | bash | diagnostics zip contents scanned for key patterns | 0 hits | `scripts/key-leak-scan.sh diagnostics.zip` | derived: friend-usable |
| ISC-42 | bash | eval set has at least 10 labelled articles, at least 4 over 15k chars, with entities and relationships | >= 10, >= 4 | `jq` over `evals/labels.json` | derived: prove-the-loop |
| ISC-43 | eval | key-entity recall across the eval set, 3 samples | ≥ 0.8 | `uv run python -m respec_worker.evals` | derived: prove-the-loop |
| ISC-43.1 | eval | key-relationship recall across the eval set, 3 samples | >= 0.6 | `uv run python -m respec_worker.evals` | derived: prove-the-loop |
| ISC-62 | eval | key-event recall across the eval set, 3 samples | >= 0.6 | `uv run python -m respec_worker.evals` | derived: prove-the-loop |
| ISC-44 | bash | worker tests: fabricated sentence dropped; curly-quote and whitespace variants matched | pass | `uv run pytest -k verbatim` | derived: firewall |
| ISC-44.1 | bash | worker test: offsets slice the document text back to the stored sentence | equal | `uv run pytest -k offsets` | derived: firewall |

## Decisions

- 2026-10-01: **Fresh repo.** Specter is frozen at `17a94aa` as reference. Material worth porting is copied to `carryover/specter/` and rewritten under test; nothing is imported.
- 2026-10-01: **Name.** Respec, the RPG term for resetting a build and reallocating its points.
- 2026-10-01: **Store.** mnestic, Rust-owned, exact-pinned; `~/projects/iw` already runs it at 0.18. Plain append-only files are the truth and the store is a rebuildable index, which hedges its pre-1.0 storage-format breaks. Rejected:
  - Neo4j Aura: shared-cloud fragility, roughly 7 to 8k of Specter's 20k backend lines were workarounds.
  - Kuzu: archived October 2025.
  - Plain SQLite: viable, but loses built-in as-of history and search fusion. It stays the fallback if mnestic breaks.
- 2026-10-01: **No shared data.** Each operator runs his own instance.
- 2026-10-01: **Rust/Python split.** Rust owns the store, the domain invariants, the API, the job runner and serving the UI. Python is a stateless subprocess per job that fetches, calls models and validates output, reporting progress as JSON Lines (the `iw` pattern). Rejected: PyO3 embedding, a long-running Python service, Docker.
- 2026-10-01: **Web app, macOS.** The friend can install by following instructions but cannot work at the command line.
- 2026-10-01: **Free models only.** At least two providers, switchable in a settings page that describes each model's spec. The friend uses his own provider account and keys.
- 2026-10-01: **Exit criterion ratified.** Five real URLs extract end to end before any other feature. v1 is done when ISC-40 holds.
- 2026-10-01: **Planning lives in this ISA and git.** No epic or plan-file sprawl; Specter produced 18.6k lines of plans for about 34k lines of product.
- 2026-10-01: **Specter's unbuilt epics.** 55 (corpus cleanup) and 58 (run history compare) are dropped as symptoms of the old architecture. 56 became ISC-31, 57 became ISC-30, and 59's hardening list feeds F0/F1 when its items apply.
- 2026-10-01: **Startup on the friend's Mac.** The installer registers a login LaunchAgent and he opens a bookmark; no app wrapper. ISC-39 verifies it. This settles the startup fog.
- 2026-10-01: **Public repo, GPL-3.0.** The repo is public on GitHub as `Enhso/respec`. It is licensed GPL-3.0 like Specter, whose code it ports. Article bodies stay out of git.
- 2026-10-01: **Plan before date.** Work order, gates and the estimate live in `PLAN.md`. The date is fixed once weekly capacity is known.
- 2026-10-01: **Red-team pass** on this ISA and `PLAN.md` (top-rung read-only agent). All critical and major findings adopted, none rebutted:
  - test the friend's keys and Mac in week one (ISC-45);
  - daily-quota handling, fallback and plain failure messages (ISC-14.1, 52, 53, 54);
  - quality gate moved before the store and server;
  - probes that could not fail were tightened;
  - firewall holes closed (ISC-56, 57, 58, 10);
  - Host and Origin checks (ISC-46, 47);
  - locked builds, single instance, torn-line tolerance, schema-marker rebuild (ISC-48 to 51);
  - masked keys and a diagnostics zip (ISC-59, 60).
  Proposed cuts: Events, second-order ego expansion, the shared side panel (ISC-31), and the search timing claim (ISC-28).
- 2026-10-01: refined: ISC-5, 10, 11, 13, 14, 22, 23, 27, 38, 42 and 44 were tightened so each probe can actually fail. ISC-14, 23, 43 and 44 gained children (.1).
- 2026-10-01: **No LiteLLM.** Its model-prefix routing caused Specter's transport errors. Its PyPI package was compromised on 2026-03-24 (versions 1.82.7 and 1.82.8). Specter's lock has 1.90.0, so this machine was not exposed. Both providers expose OpenAI-compatible chat, so one small client with a fixed host table replaces it, and ISC-37 holds by construction.
- 2026-10-01: **Fictional fixtures only.** Specter's seed attached an unverified intelligence affiliation to a real, named businessman. It is dropped from `carryover/`, the Vision uses an invented name, and the seed and debug records are removed from `corpus/`. Specter's repo is private (the GitHub API returns 404), so the label was never public. Respec's first commit was rewritten before any push.
- 2026-10-01: **Cuts ratified by the principal:** second-order expansion (ISC-30 refined), the shared side panel (ISC-31 dropped), and the search timing (ISC-28 refined). 
- 2026-10-01: **Events are a kind of entity.** Specter's separate third pass produced no Events in production; both stored Events were hand-entered. So pass 1 extracts Events with their date and place, pass 2 links participants to them, and matching reuses entity matching (ISC-61). Gate A measures event recall (ISC-62). Cost: longer pass-1 output on long articles.
- 2026-10-01: **Estimate multiplier: 2x for 50%, 3x for 80%.** Specter overran its plan by at least 4x and never finished. The multiplier is re-fit on actuals at the loop gate.
- 2026-10-01: **Skills for the restart.**
  - Before building: FirstPrinciples, RedTeam on this ISA, prototype for the store schema fog.
  - During: tdd, codebase-design for the store seam, Evals for F7, to-issues and handoff for slices.
  - At the end: golden-spec-create.
  - Avoided: the womm design-doc, epic-breakdown and implement chain.
- 2026-10-02: **First-principles pass** on the store, the web UI, the operator count and the build order. Findings:
  - No v1 claim uses mnestic's as-of history or search fusion, and Specter's whole corpus produced about 150 entities and 118 relationships, so SQLite as the truth would drop ISC-5, 50 and 51. The principal kept mnestic (`docs/adr/0001`).
  - The web UI and one instance per operator hold. Pairing call 1 tests the install path.
  - The ego graph (ISC-30) rests on no request from the friend; it is the first cut if the estimate slips.
  - The biggest untested assumption is that the friend will review proposals at all. Pairing call 1 gains a five-minute check of it (PLAN S2).
- 2026-10-02: **Domain docs split out.** The glossary moved from this ISA to `CONTEXT.md`, which adds Proposal, Candidate, Entity, Event, Relationship and Operator. ADRs 0001 to 0004 in `docs/adr/` record the store, the Rust/Python split, one instance per operator, and Events as Entities. This ISA keeps the dated log.
- 2026-10-02: **"Event" means only the domain Event.** The worker's JSON Lines are progress messages; ISC-14, 14.1 and 35 were reworded.
- 2026-10-02: **Issues only for multi-session blocks.** `/to-issues` slices S2, S3, S6 and S8 into `.scratch/` issues at their start, after a one-time `/setup-matt-pocock-skills`. Single-session rows work from their PLAN row and claims. Rewriting PLAN.md's Now section is the session handoff.
- 2026-10-02: **Date ratified.** Aim for 2027-01-16, the 50% date, and commit to 2027-03-04, the 80% date. Both are re-fit on actuals at gate B.
- 2026-10-02: **Repo live.** `github.com/Enhso/respec` is public and holds every commit so far, which the CI probes for ISC-1 and 2 rely on.
- 2026-10-02: **Specter retired.** Its Aura instance was deleted for idleness. `~/projects/specter` stays as a read-only souvenir, with its graph data in `data/dumps/2026-07-24.cypher`.
- 2026-10-02: **Foundations (PLAN S1).**
  - One crate, `respec`, edition 2024, with mnestic `=0.18.0` on its default features (bundled sqlite). The server binds `127.0.0.1:7377` and has only `/api/health`; serving `web/dist` arrives with the S2 stub page.
  - One CI workflow with four jobs (rust, python, web, isolation), green on its first run (`37050320199`, about 2.5 min cold). One workflow keeps the ISC-1 and 2 probe honest, because `runs?per_page=1` returns a single run. The isolation job runs `scripts/test-isolation.sh` on every push, so ISC-4 stays enforced rather than checked once.
  - `astral-sh/setup-uv` publishes no floating major tag, so CI pins `v10.2.0`.
  - The web shell is React 19, Vite 8 and TypeScript 7, the native compiler, which typechecks the Vite types cleanly.
- 2026-10-02: refined: the ISC-6 probe gains `-nP`. Without it `lsof` prints `localhost:7377`, which cannot tell IPv4 loopback from `::1` or a resolver quirk.
- 2026-10-02: **Agent skills configured** (`docs/agents/`).
  - `.scratch/` is gitignored, so issues stay on this machine and never reach the public repo.
  - Domain docs are multi-context: `CONTEXT-MAP.md` points at the shared `CONTEXT.md` and at `src/`, `python/` and `web/` glossaries, each created when it first needs a term. Every current term crosses the contract, so nothing moved.
  - Dev keys are the principal's own, in the local `.env`. The friend sets up his keys on his own Mac at pairing call 1.
- 2026-10-02: **S2 sliced** into six `.scratch/` issues: the page with key entry, a live test call, a long-article test extraction, the macOS release build, the installer, and pairing call 1.
  - ISC-46 and 47 move from S6 to S2. From pairing call 1 the build runs at every login on the friend's Mac, and without the Host check any page he visits could drive it through DNS rebinding.
  - ISC-37 moves from S11 to S2, because the fixed Provider-to-host table is built with the HTTP client.
- 2026-10-02: **Page, keys and the Host and Origin checks (S2 issue 01).**
  - The binary serves the built web UI from disk: `RESPEC_WEB_DIR`, default `web/dist`. It is not embedded, so the Rust build and CI stay independent of npm, and the release bundle ships the UI beside the binary.
  - Keys live in `keys.json` in the config directory: `RESPEC_CONFIG_DIR`, else `Respec` in the platform config directory (`~/Library/Application Support/Respec` on macOS). The directory is 0700 and the file 0600, written by temp file and rename. A key type with a redacted `Debug` and no `Display` or `Serialize` keeps keys out of logs and responses by construction. Keys under 8 characters are refused, so the last four shown never amount to the key.
  - The Host check admits exactly `127.0.0.1:7377` and `localhost:7377`. The Origin check covers every method but GET, HEAD and OPTIONS, and fails closed: a missing or `null` Origin gets 403, since only the page posts and browsers always send Origin on a POST. Terminal POSTs need `-H 'Origin: http://127.0.0.1:7377'`.
  - refined: the ISC-47 probe targets `POST /api/settings/keys`, because `/api/documents` does not exist until S6.
  - ISC-59 moves from S11 to S2: its probe went green from the same work.
- 2026-10-02: **Live test call through the worker (S2 issue 02).**
  - The server runs the worker's venv executable directly: `RESPEC_WORKER`, default `python/.venv/bin/respec-worker`. Running it this way needs neither uv nor a PATH at run time, which matters under launchd.
  - It clears both key variables from the inherited environment and sets only the chosen Provider's (`OPENROUTER_API_KEY` or `GEMINI_API_KEY`, the dev `.env` names). Keys never go on the command line.
  - The worker's HTTP client is httpx, synchronous, with no redirects followed. Its endpoint comes only from the Provider table in `providers.py`.
  - Progress messages are JSON Lines told apart by `kind`: `started`, `done`, `failed`. `failed` carries one of six reasons: `auth`, `quota`, `rate_limit`, `model_unavailable`, `network`, `other`. One fixture per shape lives in `contracts/fixtures/messages/`. This starts ISC-17, which stays open until the extraction messages join it in S3.
  - Default free Models, chosen by live calls: `nvidia/nemotron-3-super-120b-a12b:free` on OpenRouter and `gemini-flash-lite-latest` on Gemini. Choosing a Model stays S11.
- 2026-10-02: **macOS release bundle (S2 issue 04).**
  - The bundle mirrors the repo: `respec`, `web/dist/`, and `python/` holding only `pyproject.toml`, `uv.lock` and `src/`, plus `LICENSE` and `README.md`. Run from the bundle root, every cwd-relative default works, including the worker after `uv sync --frozen --no-dev`.
  - `scripts/bundle.sh` builds it and `scripts/smoke-bundle.sh` checks it. The smoke refuses an archive listing `.env`, keys, corpus or build residue, then requires 200 from `/api/health` and the page.
  - The release is a job inside `ci.yml`, gated on `v*` tags and needing the four gate jobs, so a tag-push run is still full CI and the ISC-1 and 2 probe stays honest. A broken release job turns that run red, which the probe then reports.
  - The tag must match the Cargo version. The release is not a prerelease, because the installer reads `releases/latest`, which skips prereleases.
- 2026-10-03: **Test extraction of a long article (S2 issue 03).**
  - `respec-worker test-extraction` fetches with Specter's ported fetch (httpx, trafilatura, bs4, made synchronous), then makes one Pass 1 call with Specter's system and Pass 1 prompts copied as is. S3 reshapes them.
  - The output budget is a fixed 16,384 tokens (both default Models allow far more), with a 600 s read timeout. Per-minute 429s are retried up to 6 attempts, honouring `Retry-After` clamped to 1 to 120 s, with a `waiting_rate_limit` progress message before each wait.
  - New progress message kinds: `progress` (stages `fetching`, `calling_model` and `waiting_rate_limit`) and `entities`. New failure reasons: `fetch` and `bad_output`. Both contract tests fail unless every kind, stage and reason has a fixture.
  - The server keeps one test extraction in memory (409 while one runs) and the page polls `GET /api/test-extraction`. This is a stand-in until S6's job runner.
  - Unparseable worker lines are no longer logged in part, because they could now hold article text. The worker writes UTF-8 bytes to stdout: a text write crashed on Chinese names when stdout was ASCII.
  - Ported: `fetch.py`, the four HTML fixtures (renamed, every name invented) and both prompts, now deleted from `carryover/`. `client.py` and `extraction_schemas.py` stay for their Pass 2 and 3 parts.
- 2026-10-03: **One-command install with a login agent (S2 issue 05).**
  - `install.sh` checks for an Apple Silicon Mac. It installs uv into `~/.local/bin` if uv is missing, without touching shell profiles. It downloads the `releases/latest` arm64 asset with curl, so no quarantine attribute is set, and replaces `~/Library/Application Support/Respec/app` wholesale. `keys.json` beside `app/` is never touched. Then it runs `respec setup`.
  - `respec setup` runs `uv sync --frozen --no-dev` and writes the LaunchAgent `io.github.enhso.respec`. It then boots out any old job and bootstraps the new one in `gui/<uid>`. The plist uses absolute paths only and sets `RESPEC_CONFIG_DIR`, `RESPEC_WEB_DIR` and `RESPEC_WORKER` explicitly, with `RunAtLoad`, `KeepAlive` and logs in `~/Library/Logs/Respec/`.
  - `scripts/install-check.sh` runs in the `release` job before publishing, under a fresh HOME. It covers uv missing from PATH, two installs, a key kept across them, exactly one plist, and a killed process restarted by launchd. `workflow_dispatch` runs the whole workflow, publish excepted.
  - `v0.1.0` predates `respec setup`, so the friend's install needs a newer release. `v0.2.0` is that release: its tag run `37157808655` passed the install check on the macOS runner before publishing.
- 2026-10-07: **S3 sliced** into six `.scratch/` issues: two-pass extraction with Events, verbatim sentences with offsets, output budget and truncation, no cache directives to Gemini, the daily-quota pause, and fallback with plain failure messages. The last two wait for pairing call 1, because the friend's accounts may change how they should work.
- 2026-10-07: **URL canonicalisation moves to S6.** No claim needs it, and Documents are created in Rust. S6 decides whether duplicate Documents earn a claim; if not, Specter's `canonicalize.py` is dropped with a row here.
- 2026-10-07: **Labels are drafted by an agent and checked by the principal** (S4 and S9). He would rather verify than generate, since he lacks the domain expertise to label well. To limit anchoring on the draft, the drafter is a Claude model and never one of the free Models under test, and his check asks what is missing before what is wrong.
- 2026-10-07: **Two-pass extraction (S3 issue 01).**
  - `respec-worker extract` takes the Document by `--url` or `--text-file`, so the S4 evals run on local corpus bodies. An unreadable or empty file fails with reason `fetch`.
  - The worker gives each Pass 1 Entity a short id, and Pass 2 refers to Entities only by id. An `entities` message follows Pass 1, and a `relationships` message ends the run. Progress during a pass carries `pass_number` and `pass_count`.
  - Events carry `date` (partial ISO: year, year-month or full date) and `place`, each null when the article does not say. The date's form is its precision; a separate precision field was one more thing for the Model to get wrong.
  - The one participant type is Specter's `PARTICIPATED_IN`. Its target must be an Event and its source must not be. Specter's `event_candidate` flags and Pass 3 are gone.
  - Replies are validated item by item (ISC-63). A malformed item, an unknown id or a bad `PARTICIPATED_IN` end is dropped and counted. The drop is logged with its error type and field, never content. Unknown keys are ignored and key whitespace is stripped, because one live draw lost 64 of 67 Entities to an invented key.

## Learning

- conjectured: "this account currently has no free model that survives a real-sized extraction call" (Specter log, 2026-07-30).
  - refuted by: the 2026-10-01 probe on a 28.5k-character Insider investigation. Output stopped at exactly 4,096 tokens, Specter's hardcoded `PASS1_MAX_TOKENS`, giving `missing_json`. With a 16k cap it returned complete JSON: 7,930 output tokens in 213 s, after four 429 retries.
  - learned: the failure was the output budget plus a shared, rate-limited free pool, not the provider.
  - criterion now: ISC-12, ISC-13, ISC-14.
- conjectured: passing the live smoke on Hatim's account proves the pipeline works for the friend.
  - refuted by: Specter's free-tier failures came from account state: an OpenRouter provider allowlist, and Gemini projects with zero quota on dated models and on cached content (`specter/docs/log.md`, lines 4807 to 4946). `iw`'s key reaches a single free model.
  - learned: provider access is a property of each account, so it has to be tested on the friend's own keys and Mac.
  - criterion now: ISC-45, in week one.
- conjectured: any listed free Model is a safe default.
  - refuted by: live calls on 2026-10-02.
    - On OpenRouter, `google/gemma-4-31b-it:free`, `iw`'s Model, returned 429 on every try, and `qwen/qwen3.8-27b:free` on two of three.
    - On Gemini, pinned Models such as `gemini-2.5-flash-lite` return 404 "no longer available to new users". The full Flash Models took 24 s or more on a one-word prompt, against under 1 s for Flash-Lite.
  - learned: free-Model availability is volatile and differs per account, so a hardcoded default will rot. The test call's 60 s client timeout suits a one-word reply only; S2 issue 03's real-sized pass needs a far longer read timeout (the 2026-10-01 probe took 213 s).
  - criterion now: ISC-52 (fallback across Models), ISC-34 and 35 (Model choice in settings).
- conjectured: one extraction run shows what a Model will propose for an article.
  - refuted by: four Pass 1 runs of `gemini-flash-lite-latest` on the same 28.5k-character Insider article on 2026-10-02 and 03, which returned 61, 67, 30 and 56 entities. Each run took 12 to 21 s, against 213 s for the 2026-10-01 probe on an OpenRouter free Model. OpenRouter's nemotron took 49 s and proposed 20.
  - learned: run-to-run variance is large, so recall needs several samples per article, and the friend's five-minute check sees a single draw.
  - criterion now: ISC-43, 43.1 and 62 already score 3 samples; keep that in S4.
- conjectured: validating a Model's reply as one whole object is safe, since a malformed reply is rare.
  - refuted by: four live Pass 1 calls of `gemini-flash-lite-latest` on a 33k-character corpus article on 2026-10-07. Three failed as `bad_output`, each on one item out of 30 to 38 with a garbled key, such as `supporting_sentences` with a leading space. With that item dropped, Pass 2 validated on both runs that reached it.
  - learned: free Models garble single items often, and one bad item must not cost the whole reply. This is Specter's 29-runs-0-successes failure in small.
  - criterion now: ISC-63.

## Verification

- ISC-1: CI run `37050320199` green, with `fmt`, `clippy` and `test` as separate steps (commit `62458c5`).
- ISC-2: CI run `37050320199` green, with `ruff`, `mypy` and `pytest` as separate steps (commit `62458c5`).
- ISC-3: `grep` matches `mnestic = "=0.18.0"` (commit `62458c5`).
- ISC-4: `scripts/test-isolation.sh` green. A planted test writing `~/leak.txt` turned it red (commit `62458c5`).
- ISC-6: `lsof -nP` shows only `127.0.0.1:7377`, and the LAN address refuses connections (commit `62458c5`).
- ISC-48: `grep` finds `--locked` and `--frozen` in `ci.yml`, and all three lock files are tracked (commit `62458c5`).
- ISC-46: the probe returned 200 at `b229646` and 403 after the build. `cargo test foreign_host_rejected` covers `/api/health` and `/`.
- ISC-47: the retargeted probe returned 404 at `b229646` and 403 after the build. `cargo test foreign_origin_rejected` also rejects a missing and a `null` Origin. Removing either check turns its test red.
- ISC-59: `curl -s localhost:7377/api/settings` returned 404 at `b229646`. After saving a fake key it returned only its last four (`wxyz`). The full key had 0 hits in the server log, and `keys.json` was mode 600 in a 700 directory.
- ISC-37: `uv run pytest -k provider_hosts` selected no tests at `b6acc73`, so pytest exited 5. After the build it passes 4. Pointing Gemini's URL at OpenRouter's host turned it red. Live, both Providers answered through `POST /api/test-call`, and their keys had 0 hits in the server log.
- ISC-54: `uv run pytest -k gemini_no_cache` selected no tests at `5b2c172` (exit 5) and passes 2 after the build. Planting `cachedContent` in the payload, and separately a `cached_content` key nested two levels deep, turned both tests red.
- ISC-61: `uv run pytest -k two_pass_events` selected no tests at `5b2c172` (exit 5) and passes 14 after the build. Live on Gemini, `extract` ran both passes on two corpus bodies (28.9k and 33k characters) and one Insider URL: 31 to 63 Entities, 1 to 4 Events each with date and place, 22 to 38 Relationships, in 17 to 60 s.
- ISC-63: `uv run pytest -k malformed_item` selected no tests at `00d683f` and passes 52 after the build. Breaking the log redaction, the dropped count or the `PARTICIPATED_IN` rule each turned a test red. Before the fix, 3 of 4 live Pass 1 replies failed whole on one garbled item.
