# Respec plan to v1

The order of work, as sessions. A session is one fresh-context work unit, roughly one sitting. It starts from `ISA.md` and ends with three things:

- commits;
- the claims it closed, ticked on their probes;
- a handoff note.

Estimates are in sessions. Dates depend on weekly capacity (see the end).

## Now

- **Stopped mid-S3 on 2026-10-07 for usage limits.** Issues 01, 02 and 04 are done (ISC-44, 44.1, 54, 61, 63 closed). Resume in this order:
  1. **Issue 03** (output budget and truncation, ISC-12, 13): a builder had it almost done in the worktree `.claude/worktrees/agent-aa296673a5509ad19`, saved as one unverified WIP commit on branch `worktree-agent-aa296673a5509ad19`. Review it against `.scratch/s3-worker/issues/03-output-budget-and-truncation.md`, re-run every gate and the live spec lookups, then merge into `main`. Expect conflicts in `messages.py`, `cli.py`, the tests and the fixtures, because issue 02 landed first. Record the spec numbers in the ISA (it settles the spec-source fog), then `git worktree remove` it and delete the branch.
  2. **Owed live check for issue 02:** run `extract --text-file` on a corpus body and confirm every emitted `sentence` equals the Document text sliced at `sentence_start:sentence_end`. Gemini answered 503 (overloaded) twice today; retry later if so.
  3. **Issues 05 and 06** wait for pairing call 1. Issue 06 carries a note on Gemini 503s. S3 then closes once ISC-17's worker side is checked.
- **Waiting on:** the friend's reply and his OpenRouter and Gemini accounts, for pairing call 1 (`.scratch/s2-tracer-to-his-mac/issues/06-pairing-call-1.md`).
- **The install command:** `curl -fsSL https://raw.githubusercontent.com/Enhso/respec/main/install.sh | bash`, run in Terminal and never downloaded through a browser. `v0.2.0` is the release he installs.
- **Run the worker:** `uv run --frozen --directory python --env-file ../.env respec-worker extract --provider gemini --text-file ../corpus/bodies/<id>.txt` (or `--url <url>`). Expect 25 to 63 Entities and 18 to 38 Relationships in 15 to 60 s.
- **Agents:** up to 2 Sonnet and 4 Haiku at once (`OPERATIONAL_RULES` § Model selection). Commits carry no Co-Authored-By trailer (a hook rejects it).
- **Gates:** CI runs rust, python, web and isolation on every push. On `v*` tags and manual runs it also runs `release` on macOS. When the web app gains tests, add them to `scripts/test-isolation.sh`.

## Rules

- **A session closes claims, not tasks.** If its claims are still open at the end, the next session finishes them before starting anything new.
- **What killed Specter gets tested first.** Specter died on two things:
  - provider account state, which varies per user;
  - an install path nobody had tried on the friend's machine.

  So the friend's keys and Mac are tested in week one (S2), before the real build.
- **Two gates, no skipping.**
  - **A, quality known:** recall is measured on labelled articles using the worker alone. No store, server or UI is needed for this.
  - **B, loop proven:** five real articles go end to end through the running app.
- **Kill switch at gate A.** If free-model extraction is too weak to be worth reviewing, stop and re-plan before building anything that depends on it.
- **Slice only the multi-session rows.** S2, S3, S6 and S8 start with `/to-issues`. Other rows work straight from their row and its claims.
- **The ISA stays the spec.** Fog gets resolved where the table says. Anything a session learns goes into the ISA in the same commit.

## Sessions

| # | Session | Claims closed | Sessions | Your time |
|---|---------|---------------|----------|-----------|
| S1 | **Foundations.** One Cargo crate (axum 0.8, tokio, mnestic exact-pinned, sqlite engine, the `iw` layout). Worker package (uv, hatchling, a `respec-worker` CLI). Vite app shell. GitHub Actions for Rust, Python and web, with locked builds. Test-isolation script and the `contracts/fixtures/` folder. | ISC-1, 2, 3, 4, 6, 48 | 1 | review |
| S2 | **Tracer to his Mac.** A stub server page with key entry and a "run test extraction" button. It runs one real-sized pass on a long Insider URL through the new HTTP client. Release build for `aarch64-apple-darwin` on a GitHub macOS runner. `install.sh` installs uv, runs `respec setup` (`uv sync --frozen`, a LaunchAgent with absolute paths), then **pairing call 1**: the friend's keys and Mac, a reboot, his bookmark, and five minutes showing him the proposals for an article he picks: would he review that list? | ISC-45, 46, 47, 37; early ISC-39; macOS 27 unknowns | 2 + call | 30 min call |
| S3 | **Worker.** Port prompts and schemas, reshaped to two passes with Events as an entity kind. Output budget from the model's spec. Truncation; per-minute and per-day 429s; fallback across providers; plain failure messages; no cache directives to Gemini. Sentence normalisation with stored offsets. JSON Lines progress messages. | ISC-12, 13, 14, 14.1, 44, 44.1, 52, 53, 54, 61; ISC-17 worker side; fog: spec source | 2 | none |
| S4 | **Gate A, quality.** An agent drafts labels of key entities, events and relationships for 10+ articles, 4+ of them over 15k characters, and you check them, asking what is missing before what is wrong. Recall eval across two or three free models, run on the worker alone. Settle chunking for long articles. **Kill switch.** | ISC-42, 43, 43.1, 62 | 1 | checking drafted labels |
| S5 | **Store.** A throwaway mnestic prototype first, then the real store module: entities, relationships, observations with valid time, full-text search, the two-hop ego query, and a rebuild from files. | fog: schema, upgrades | 1 | review |
| S6 | **Server.** Data directory with append-only files (torn-line tolerant). Documents API for URL or dated pasted text, and URL canonicalisation (decide here whether duplicate Documents earn a claim). A serial job runner that spawns the worker, saves each pass and resumes. Proposals into the store as Hypothesis. Single instance, rebuild on schema change. | ISC-9, 10, 15, 49, 50, 51, 55; ISC-17 server side | 2 | none |
| S7 | **Gate B, loop proven.** Minimal UI: paste form, live job list, proposal list. Five-article live smoke run plus a key-leak scan. Re-fit the estimate on actuals. | ISC-11, 16, 7 | 1 | watch the smoke run |
| S8 | **Review and the firewall.** Queue grouped by document; approve, reject and edit; Knowledge writes that require an approval; endpoint and duplicate-relationship rules; integrity check with a startup banner; ported Knowledge/Hypothesis tokens. | ISC-18 to 24, 23.1, 56, 57, 58 | 2 | try the queue |
| S9 | **Entity matching.** Full-text candidates scored in Rust. Merge an entity, keeping its other name as an alias. Threshold calibrated on recurring entities, transliterations and look-alikes. Then rebuild parity over the full approve/edit/reject/merge fixture set. | ISC-25, 26, 27, 5 | 1 to 2 | checking drafted pair labels |
| S10 | **Navigation.** Search, entity profile, ego graph (Cytoscape with fcose, ported canvas styles), highlighted source sentence. The proposed cuts apply here. | ISC-28 to 32 | 1 to 2 | try it |
| S11 | **Providers and models.** Settings page with keys in `~/Library/Application Support/Respec`, masked when shown. Live model lists with specs, switching, a "test this model" button. | ISC-33 to 36, 59 | 1 to 2 | none |
| S12 | **Packaging finish.** Update path that keeps data, a diagnostics zip with no keys, written install instructions (never download through a browser; expect the Background Items notice). | ISC-41, 60; ISC-38 prep | 1 to 2 | proofread the instructions |
| S13 | **Hardening and carryover sweep.** Fetch size cap and content-type allowlist (Specter epic 59). Empty `carryover/`. | ISC-8 | 1 | none |
| T | **Handoff.** Pairing call 2 on the friend's Mac, then his unsupervised week. | ISC-38, 39, 40 | 1 to 2 weeks of calendar | 1 h call |

**Total:** 18 to 22 sessions, your checks of agent-drafted labels for S4 and S9, two calls with the friend, and a 1 to 2 week trial.

## Cuts

Ratified 2026-10-01:

- second-order ego expansion;
- the shared side panel (ISC-31);
- the search timing claim (ISC-28). Search itself stays.

Events stay, as an entity kind with no third pass (decided 2026-10-01).

## Estimate

**Reference class.** Specter planned four phases and finished them in about three weeks. Then it needed nine more weeks of fixes and never met its exit criterion: an overrun of at least 4x, with no finish. A plan built to avoid Specter's specific failures earns a smaller multiplier, but not 1x. So: **2x the raw session count for the 50% date, 3x for the 80% date.** Re-fit both on real numbers at gate B.

**Capacity.** 2 to 5 sessions a week. The start is assumed to be Monday 5 October 2026.

**Monte Carlo** (20k draws). Inputs:

- 17 to 21 sessions;
- an overrun multiplier with median 2x and 80th percentile 3x, lognormal;
- capacity uniform between 2 and 5 sessions a week;
- a 1 to 2 week trial;
- two weeks of holiday slip if the work runs past 20 December.

| Capacity | 10% | 50% | 80% | 95% |
|----------|-----|-----|-----|-----|
| always 5 a week | 2026-11-13 | 2026-12-07 | 2027-01-18 | 2027-02-25 |
| 2 to 5 a week | 2026-11-23 | **2027-01-16** | **2027-03-04** | 2027-05-13 |
| always 2 a week | 2027-01-09 | 2027-03-11 | 2027-05-19 | 2027-08-23 |

**Target (ratified 2026-10-02):** aim for 2027-01-16 (the 50% date) and commit to 2027-03-04 (the 80% date). Re-run the model on real numbers at gate B.
