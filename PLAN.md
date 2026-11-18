# Respec plan to v1

The order of work, as sessions. A session is one fresh-context work unit, roughly one sitting. It starts from `ISA.md` and ends with three things:

- commits;
- the claims it closed, ticked on their probes;
- a handoff note.

Estimates are in sessions. Dates depend on weekly capacity (see the end).

## Now

- **Handoff of 2026-11-18:** `.scratch/s4-gate-a/handoff.md` (local) holds the detail, the commands and the next steps. Read it first.
- **The principal's calls of 2026-11-18:** no pairing call, since the friend installed `v0.2.0` on his own without problems; minimal oversight, so checks are a small sample or a blind Model; v1 by 2026-12-31 at 7 or more sessions a week; push freely, and tag when ready.
- **S3 is done.** Issues 05 and 06 closed ISC-14, 14.1, 52 and 53: per-day quota as `daily_quota` with a retry time, 503 retried as `overloaded`, gated 403s told apart from bad keys, one failure-message table, `extract --fallback` across Model families, and a total deadline per call. The OpenRouter default moved to `nvidia/nemotron-3.5-super-vl:free`, because the old one is gone. Version 0.3.0. ISC-17 waits on S6's server side.
- **S4 (gate A) is open.** The drafted labels are checked by a blind second labelling plus an adjudicator, in place of the principal (Decisions, 2026-11-18). All 12 blind relabels are done, and 6 of 12 are adjudicated (`evals/labels-final/`). Set A preview, Flash-Lite pooled over 72 samples: Entities 0.93, Events 0.56, Relationships 0.56 on the adjudicated labels, against 0.48 on the drafts. Super-VL's eval is part run, with OpenRouter's free tier capped at 50 requests a day. Gemini Flash is out (20 requests a day, 503s).
- **Next, in order:** settle the sanction-encoding rule in `evals/README.md`; adjudicate set B (the brief is ready in the handoff folder); score both Models; put the gate verdict to the principal as one yes or no; close S4. Then send the friend's self-check message (in the handoff) once `v0.3.0` is green, and start S5.
- **Waiting on the principal:** the gate verdict (one yes or no, next session); forwarding one message to the friend after the release; and yes or no on the scope cuts under Estimate.
- **The install command:** `curl -fsSL https://raw.githubusercontent.com/Enhso/respec/main/install.sh | bash`, run in Terminal and never downloaded through a browser. Re-running it upgrades in place and keeps the keys.
- **Run the worker:** `uv run --frozen --directory python --env-file ../.env respec-worker extract --provider gemini [--model M] [--fallback] --text-file ../corpus/bodies/<id>.txt` (or `--url <url>`). The evals never pass `--fallback`.
- **Agents:** caps and tiers per `~/.claude/CLAUDE.md` (Model selection). Commits carry no attribution trailer (a hook rejects it). Fixtures, tests and committed docs use invented names only; labels, runs and review pages are gitignored because they name real people.
- **Gates:** CI runs rust, python, web and isolation on every push. On `v*` tags and manual runs it also runs `release` on macOS. When the web app gains tests, add them to `scripts/test-isolation.sh`.

## Rules

- **A session closes claims, not tasks.** If its claims are still open at the end, the next session finishes them before starting anything new.
- **What killed Specter gets tested first.** Specter died on two things:
  - provider account state, which varies per user;
  - an install path nobody had tried on the friend's machine.

  So the install was tested on the friend's Mac first: he ran it himself on 2026-11-18 without problems. His keys get a real-sized test extraction as soon as `v0.3.0` is out.
- **Two gates, no skipping.**
  - **A, quality known:** recall is measured on labelled articles using the worker alone. No store, server or UI is needed for this.
  - **B, loop proven:** five real articles go end to end through the running app.
- **Kill switch at gate A.** If free-model extraction is too weak to be worth reviewing, stop and re-plan before building anything that depends on it.
- **Minimal oversight (2026-11-18).** The principal checks only a small sample. Label checks go to a blind second labeller plus an adjudicator, and decisions reach him as one yes or no.
- **Slice only the multi-session rows.** S2, S3, S6 and S8 start with `/to-issues`. Other rows work straight from their row and its claims.
- **The ISA stays the spec.** Fog gets resolved where the table says. Anything a session learns goes into the ISA in the same commit.

## Sessions

| # | Session | Claims closed | Sessions | Your time |
|---|---------|---------------|----------|-----------|
| S1 | **Foundations.** One Cargo crate (axum 0.8, tokio, mnestic exact-pinned, sqlite engine, the `iw` layout). Worker package (uv, hatchling, a `respec-worker` CLI). Vite app shell. GitHub Actions for Rust, Python and web, with locked builds. Test-isolation script and the `contracts/fixtures/` folder. | ISC-1, 2, 3, 4, 6, 48 | 1 | review |
| S2 | **Tracer to his Mac.** A stub server page with key entry and a "run test extraction" button. It runs one real-sized pass on a long Insider URL through the new HTTP client. Release build for `aarch64-apple-darwin` on a GitHub macOS runner. `install.sh` installs uv, runs `respec setup` (`uv sync --frozen`, a LaunchAgent with absolute paths), then the friend installs it himself (done 2026-11-18, no call). His keys, a reboot and his review willingness come back as a self-check message after `v0.3.0`. | ISC-45, 46, 47, 37; early ISC-39; macOS 27 unknowns | 2 | forward one message |
| S3 | **Worker.** Port prompts and schemas, reshaped to two passes with Events as an entity kind. Output budget from the model's spec. Truncation; per-minute and per-day 429s; fallback across providers; plain failure messages; no cache directives to Gemini. Sentence normalisation with stored offsets. JSON Lines progress messages. | ISC-12, 13, 14, 14.1, 44, 44.1, 52, 53, 54, 61; ISC-17 worker side; fog: spec source. Done 2026-11-18. | 2.5 | none |
| S4 | **Gate A, quality.** An agent drafts labels of key entities, events and relationships for 10+ articles, 4+ of them over 15k characters. A second agent relabels them blind, and an adjudicator settles every disagreement. Recall eval across two or three free models, run on the worker alone. Settle chunking for long articles. **Kill switch.** | ISC-42, 43, 43.1, 62 | 2 to 3 | one yes or no on the verdict |
| S5 | **Store.** A throwaway mnestic prototype first, then the real store module: entities, relationships, observations with valid time, full-text search, the two-hop ego query, and a rebuild from files. | fog: schema, upgrades | 1 | review |
| S6 | **Server.** Data directory with append-only files (torn-line tolerant). Documents API for URL or dated pasted text, and URL canonicalisation (decide here whether duplicate Documents earn a claim). A serial job runner that spawns the worker, saves each pass and resumes. Proposals into the store as Hypothesis. Single instance, rebuild on schema change. | ISC-9, 10, 15, 49, 50, 51, 55; ISC-17 server side | 2 | none |
| S7 | **Gate B, loop proven.** Minimal UI: paste form, live job list, proposal list. Five-article live smoke run plus a key-leak scan. Re-fit the estimate on actuals. | ISC-11, 16, 7 | 1 | watch the smoke run |
| S8 | **Review and the firewall.** Queue grouped by document; approve, reject and edit; Knowledge writes that require an approval; endpoint and duplicate-relationship rules; integrity check with a startup banner; ported Knowledge/Hypothesis tokens. | ISC-18 to 24, 23.1, 56, 57, 58 | 2 | try the queue |
| S9 | **Entity matching.** Full-text candidates scored in Rust. Merge an entity, keeping its other name as an alias. Threshold calibrated on recurring entities, transliterations and look-alikes. Then rebuild parity over the full approve/edit/reject/merge fixture set. | ISC-25, 26, 27, 5 | 1 to 2 | none (blind pair labels) |
| S10 | **Navigation.** Search, entity profile, ego graph (Cytoscape with fcose, ported canvas styles), highlighted source sentence. The proposed cuts apply here. | ISC-28 to 32 | 1 to 2 | try it |
| S11 | **Providers and models.** Settings page with keys in `~/Library/Application Support/Respec`, masked when shown. Live model lists with specs, switching, a "test this model" button. | ISC-33 to 36, 59 | 1 to 2 | none |
| S12 | **Packaging finish.** Update path that keeps data, a diagnostics zip with no keys, written install instructions (never download through a browser; expect the Background Items notice). | ISC-41, 60; ISC-38 prep | 1 to 2 | proofread the instructions |
| S13 | **Hardening and carryover sweep.** Fetch size cap and content-type allowlist (Specter epic 59). Empty `carryover/`. | ISC-8 | 1 | none |
| T | **Handoff.** He upgrades with the install command and uses it unsupervised for a week. No call unless something breaks. | ISC-38, 39, 40 | 1 week of calendar | forward the release note |

**Total:** 18 to 22 sessions in the first plan. As of 2026-11-18, 13 to 17 remain, plus a one-week trial and a few yes-or-no calls from the principal.

## Cuts

Ratified 2026-10-01:

- second-order ego expansion;
- the shared side panel (ISC-31);
- the search timing claim (ISC-28). Search itself stays.

Events stay, as an entity kind with no third pass (decided 2026-10-01).

## Estimate

**Reference class.** Specter planned four phases and finished them in about three weeks. Then it needed nine more weeks of fixes and never met its exit criterion: an overrun of at least 4x, with no finish. A plan built to avoid Specter's specific failures earns a smaller multiplier, but not 1x. So: **2x the raw session count for the 50% date, 3x for the 80% date.** Re-fit both on real numbers at gate B. Actuals so far run at about 1.3x per session, but six weeks passed with no session while work waited on people. Waiting, not session overrun, is what slipped the first plan.

**Re-fit 2026-11-18** (`.scratch/s4-gate-a/mc.py`, 20k draws, start 2026-11-18). Inputs:

- 13 to 17 sessions left;
- an overrun multiplier with median 2x and 80th percentile 3x, lognormal;
- the stated capacity;
- a trial of 1 to 1.5 weeks;
- no holiday slip, by the principal's commitment.

| Scenario | 10% | 50% | 80% | 95% | v1 by Dec 31 |
|----------|-----|-----|-----|-----|--------------|
| as planned, 7 a week | 2026-12-12 | 2026-12-26 | 2027-01-11 | 2027-02-01 | 63% |
| as planned, 5 to 7 a week | 2026-12-15 | 2026-12-31 | 2027-01-19 | 2027-02-14 | 50% |
| with the cuts below, 7 a week | 2026-12-08 | 2026-12-19 | 2026-12-31 | 2027-01-17 | 81% |

**Target (the principal's call, 2026-11-18):** v1 by 2026-12-31 at 7 or more sessions a week. The first plan aimed at 2027-01-16 and committed to 2027-03-04.

**Proposed cuts, waiting on the principal:**
- S11 shrinks to the fallback list plus key entry: drop ISC-34, 35 and 36.
- Drop ISC-60, the diagnostics zip.
- Fold S13 into the sessions that touch its files.

Together they save about 3 sessions. The cost: a rotted Model is replaced by a release, not by the friend in settings.
