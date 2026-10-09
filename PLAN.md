# Respec plan to v1

The order of work, as sessions. A session is one fresh-context work unit, roughly one sitting. It starts from `ISA.md` and ends with three things:

- commits;
- the claims it closed, ticked on their probes;
- a handoff note.

Estimates are in sessions. Dates depend on weekly capacity (see the end).

## Now

- **S4 (gate A) is half done, stopped 2026-10-09 on usage limits.** Built and committed: the looser verbatim matcher and the fetch fix (`49b1ebb`), the eval tooling in `python/src/respec_worker/evals.py` and `review.py` (`c796e4c`), the prompt round with `SUPPLIED` (`591cb43`), the closest-name matching rule (`d5ff67c`) and an off-by-default Pass 2 chunking option (`2427ce3`). `evals/README.md` explains the layout, the commands and the scoring rules. ISA Decisions and Learning of 2026-10-09 hold the reasoning.
- **Results so far** (Flash-Lite, new prompt `e270fd4d`, 12 articles x 3 samples, labels drafted but NOT yet checked): Entities 0.89 (gate 0.8, passes), Events 0.62 (gate 0.6, passes), Relationships 0.45, any type 0.50 (gate 0.6, fails; long articles 0.34, short 0.54). On the 8 held-out articles: 0.91, 0.63, 0.40. The old prompt (`49e5f6e8`) gave 0.86, 0.25, 0.30. Chunking is settled: no (ISA Decisions). Review load rose from about 51 to 76 Proposals per article.
- **Next, in order:**
  1. **The principal checks the 12 drafted labels**: open `evals/review/index.html` (regenerate with `uv run --frozen python -m respec_worker.evals review` from `python/`). Each page shows the article first, then the lists and the drafter's doubts. Ask what is missing, then what is wrong. Drafters flagged over-inclusion (list mentions kept as key) and a few inferred links; each file's notes say what to cut. Apply his corrections to `evals/labels/*.json`, set `"checked": true`, rerun `lint` (ISC-42 closes when it prints 12 checked, 5 long, 0 problems) and re-score; runs are cached, so no Model calls.
  2. **Finish the Model comparison.** Nemotron on OpenRouter: resume with `uv run --frozen --env-file ../.env python -m respec_worker.evals run --provider openrouter --model nvidia/nemotron-3-super-120b-a12b:free` (free tier, 50 requests a day; it failed 3 of 3 on the 28k article `34b81ba4` with "not valid JSON", cause unknown, while a short article parsed fine). Gemini Flash (`gemini-flash-latest`) returned 503 "high demand" all day; retry it.
  3. **Gate call with the principal.** Relationships are the open question: ISC-43.1's 0.6 is marked provisional, to be re-set at this gate. Options to put to him: accept a lower bar given checked-label numbers, try one more Pass 2 lever (long articles are the gap), or trip the kill switch. Record the verdict in Decisions, tick ISC-42, 43, 43.1 and 62 with Verification rows, and close S4.
- **S3 is blocked on the friend.** Issues 05 and 06 (ISC-14, 14.1, 52, 53) wait for pairing call 1 (`.scratch/s2-tracer-to-his-mac/issues/06-pairing-call-1.md`). Issue 06 now records that Gemini 503s were the commonest failure today, Flash-Lite included, so a 503 needs the 429 retry path and fallback should change Model family. ISC-17's worker side follows them.
- **The install command:** `curl -fsSL https://raw.githubusercontent.com/Enhso/respec/main/install.sh | bash`, run in Terminal and never downloaded through a browser. `v0.2.0` is the release he installs; it predates the fetch fix.
- **Run the worker:** `uv run --frozen --directory python --env-file ../.env respec-worker extract --provider gemini [--model M] --text-file ../corpus/bodies/<id>.txt` (or `--url <url>`). With the new prompt expect 30 to 70 Entities and 20 to 60 Relationships in 10 to 40 s.
- **Agents:** up to 2 Sonnet and 4 Haiku at once (`OPERATIONAL_RULES` § Model selection). Commits carry no Co-Authored-By trailer (a hook rejects it). Fixtures, tests and committed docs use invented names only; labels, runs and review pages are gitignored because they name real people.
- **Gates:** CI runs rust, python, web and isolation on every push. On `v*` tags and manual runs it also runs `release` on macOS. When the web app gains tests, add them to `scripts/test-isolation.sh`. Six local commits (from `49b1ebb` on) are not pushed yet.

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
