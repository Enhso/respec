# Respec plan to v1

The order of work, as sessions. A session is one fresh-context work unit, roughly one sitting. It starts from `ISA.md` and ends with three things:

- commits;
- the claims it closed, ticked on their probes;
- a handoff note.

Estimates are in sessions. Dates depend on weekly capacity (see the end).

## Now

- **Next session:** S2, wave 3: issues 03 (test extraction of a long article) and 05 (one-command install) can run in parallel. Then the pairing call (06).
- **Before 05:** push a `v0.1.0` tag. It runs the release job on the macOS runner, which is still unproven there, and publishes the first release that `install.sh` downloads.
- **Waiting on:**
  - the friend's OpenRouter and Gemini accounts, needed only by the S2 pairing call. He sets up his keys on his own Mac, so building S2 does not wait on them.
- **Where S2 stands:** issues 01, 02 and 04 are built (ISC-37, 46, 47 and 59 closed; ISC-17 started).
  - `respec` serves `web/dist` and a stub settings page: key entry, last four shown, a "Test call" button per Provider. Every POST needs `Origin: http://127.0.0.1:7377` or `http://localhost:7377`.
  - The test call runs the worker's venv executable (`RESPEC_WORKER`, default `python/.venv/bin/respec-worker`). Only the chosen key is passed, in the worker's environment, and the worker reports JSON Lines matching `contracts/fixtures/messages/`.
  - Default free Models: `nvidia/nemotron-3-super-120b-a12b:free` and `gemini-flash-lite-latest`. The client's 60 s timeout suits only the test call; 03 needs a far longer one.
  - `scripts/bundle.sh` and `scripts/smoke-bundle.sh` build and check the release bundle. The tag-gated `release` job in `ci.yml` publishes it, but has not yet run on macOS.
  - `src/store.rs` opens mnestic, but `main` opens no store.
- **S2 issues:** six in `.scratch/s2-tracer-to-his-mac/issues/`, 01 to 05 for agents and 06 the pairing call.
- **Dev keys:** live model calls during development read your own OpenRouter and Gemini keys from the local, gitignored `.env`. From the terminal: `uv run --frozen --directory python --env-file ../.env respec-worker test-call --provider gemini`.
- **Gates:** CI runs four jobs on every push: rust, python, web and isolation, plus `release` on `v*` tags. When the web app gains tests, add them to `scripts/test-isolation.sh`.

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
| S3 | **Worker.** Port fetch, canonicalisation, prompts and schemas, reshaped to two passes with Events as an entity kind. Output budget from the model's spec. Truncation; per-minute and per-day 429s; fallback across providers; plain failure messages; no cache directives to Gemini. Sentence normalisation with stored offsets. JSON Lines progress messages. | ISC-12, 13, 14, 14.1, 44, 44.1, 52, 53, 54, 61; ISC-17 worker side; fog: spec source | 2 | none |
| S4 | **Gate A, quality.** You label key entities, events and relationships for 10+ articles, 4+ of them over 15k characters. Recall eval across two or three free models, run on the worker alone. Settle chunking for long articles. **Kill switch.** | ISC-42, 43, 43.1, 62 | 1 | about 3 h labelling |
| S5 | **Store.** A throwaway mnestic prototype first, then the real store module: entities, relationships, observations with valid time, full-text search, the two-hop ego query, and a rebuild from files. | fog: schema, upgrades | 1 | review |
| S6 | **Server.** Data directory with append-only files (torn-line tolerant). Documents API for URL or dated pasted text. A serial job runner that spawns the worker, saves each pass and resumes. Proposals into the store as Hypothesis. Single instance, rebuild on schema change. | ISC-9, 10, 15, 49, 50, 51, 55; ISC-17 server side | 2 | none |
| S7 | **Gate B, loop proven.** Minimal UI: paste form, live job list, proposal list. Five-article live smoke run plus a key-leak scan. Re-fit the estimate on actuals. | ISC-11, 16, 7 | 1 | watch the smoke run |
| S8 | **Review and the firewall.** Queue grouped by document; approve, reject and edit; Knowledge writes that require an approval; endpoint and duplicate-relationship rules; integrity check with a startup banner; ported Knowledge/Hypothesis tokens. | ISC-18 to 24, 23.1, 56, 57, 58 | 2 | try the queue |
| S9 | **Entity matching.** Full-text candidates scored in Rust. Merge an entity, keeping its other name as an alias. Threshold calibrated on recurring entities, transliterations and look-alikes. Then rebuild parity over the full approve/edit/reject/merge fixture set. | ISC-25, 26, 27, 5 | 1 to 2 | about 1 h labelling |
| S10 | **Navigation.** Search, entity profile, ego graph (Cytoscape with fcose, ported canvas styles), highlighted source sentence. The proposed cuts apply here. | ISC-28 to 32 | 1 to 2 | try it |
| S11 | **Providers and models.** Settings page with keys in `~/Library/Application Support/Respec`, masked when shown. Live model lists with specs, switching, a "test this model" button. | ISC-33 to 36, 59 | 1 to 2 | none |
| S12 | **Packaging finish.** Update path that keeps data, a diagnostics zip with no keys, written install instructions (never download through a browser; expect the Background Items notice). | ISC-41, 60; ISC-38 prep | 1 to 2 | proofread the instructions |
| S13 | **Hardening and carryover sweep.** Fetch size cap and content-type allowlist (Specter epic 59). Empty `carryover/`. | ISC-8 | 1 | none |
| T | **Handoff.** Pairing call 2 on the friend's Mac, then his unsupervised week. | ISC-38, 39, 40 | 1 to 2 weeks of calendar | 1 h call |

**Total:** 18 to 22 sessions, about 4 hours of your labelling, two calls with the friend, and a 1 to 2 week trial.

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
