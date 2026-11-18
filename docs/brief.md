# Respec brief

As of 2026-11-18. Rewritten every session; the spec is `ISA.md`, the order of work `PLAN.md`.

## What it is

Respec turns long investigative articles (The Insider, Bellingcat) into a sourced graph of people, organisations, vessels, places and Events. A free LLM proposes Entities and Relationships, each tied to a verbatim sentence of the article. The Operator approves, edits or rejects each one, and only approved items become Knowledge. It runs as one local instance per Operator on his own Mac, on free model tiers.

The first Operator is a researcher who tracks Russian state-adjacent networks and is not at home in a terminal. v1 is done when five real articles go end to end through the app on free Models, and he ingests and reviews an article we did not prepare, unsupervised, within a week. The target is 2026-12-31.

Respec restarts Specter, which never ran end to end: 29 recorded extraction runs, 0 successes. Its failures came from provider account state and a 4,096-token output cap, and its user was never set up on it. Respec's order of work is built to hit those failure points first.

## Where it stands

- **Built:** CI with locked builds; the Rust server shell with key storage and Host and Origin checks; the macOS release, the one-command installer and a login agent; and the extraction worker. The worker makes two passes, grounds every item in a verbatim sentence, sizes its output budget from each Model's spec, and detects truncation. It retries per-minute limits and outages, stops at once on a daily quota and says when to retry, and with `--fallback` moves a failing pass to the next Model in a list that spans two Model families. Also built: the quality eval with its labelled set. Version 0.3.0.
- **Not built:** the store, the job runner and Documents API, the review queue, entity matching, navigation, the settings page.
- **In progress:** the quality gate. All 12 articles have a blind second labelling, and half are adjudicated. The second Model's eval is part run.
- **Unverified on the Operator's Mac:** he installed `v0.2.0` himself without problems, but a real-sized extraction on his own keys has not run. That check follows the `v0.3.0` release.

## Architecture

- **Rust server (axum, tokio), one binary.** It owns all state and the domain rules, serves the JSON API and the built web UI from one origin on `127.0.0.1:7377`, and will run the job queue. It accepts only its own Host header and requires its own Origin on every state-changing request, since a loopback bind alone does not stop DNS rebinding or cross-site posts from pages the Operator visits.
- **Store: mnestic, embedded, exact-pinned (`=0.18.0`).** Append-only plain files in the data directory are the source of truth, and the store is an index rebuilt from them. That hedges mnestic's pre-1.0 storage-format breaks: an upgrade is a rebuild. Not built yet.
- **Python worker, one subprocess per job, stateless.** It fetches the article, calls the Model and validates the output, reporting progress as JSON Lines on stdout. It never opens the store. Both test suites load the same contract fixtures, so the two sides cannot drift silently.
- **Model client: Respec's own, about 200 lines** over the Providers' OpenAI-compatible endpoints, with a fixed Provider-to-host table, so a key can only ever reach its own Provider. Keys live in the Operator's config directory (mode 600) and are never returned by the API; only their last four characters are shown.
- **Packaging.** An `aarch64-apple-darwin` release is built on a GitHub macOS runner. `install.sh` installs uv, downloads the release with curl (no quarantine flag), syncs the worker's locked environment and registers a login LaunchAgent. After that the Operator uses a bookmark.

## Decisions

| Choice | Why | Rejected | Cost |
|---|---|---|---|
| One local instance per Operator | No accounts, no hosting, no shared-data failure modes | Shared cloud graph (Specter's Neo4j Aura) | No collaboration in v1 |
| mnestic, with files as the truth | Embedded; built-in history and search; rebuildable | Neo4j (7 to 8k of Specter's 20k backend lines were workarounds); Kuzu (archived); plain SQLite (kept as fallback) | Pre-1.0 dependency |
| Rust owns state, Python stateless | Invariants in one typed place; Python keeps the ML ecosystem | PyO3 embedding; a long-running Python service | Two languages, one contract to maintain |
| Free Models only, two Providers | The Operator pays nothing | Paid Models | Rate limits, outages, volatile Model lists |
| Own HTTP client, no LiteLLM | Its prefix routing broke Specter; a compromised PyPI release in March 2026 | LiteLLM | Provider quirks are ours to handle |
| Events are Entities, two passes | Specter's separate Event pass produced none | A third pass | Longer Pass 1 output |
| A Proposal survives only with a verbatim sentence | No fact without provenance; a fabricated quote cannot pass | Trusting the Model's quote | Lost recall when the matcher is too strict (fixed, see Evidence) |
| Review before Knowledge, always | The graph stays trustworthy | Auto-accept above a confidence | Review load per article |
| No chunking of long articles | Measured: no gain (see Evidence) | Pass 2 per 8k-character part | Long articles recall fewer links |
| Ordered fallback across Model families, opt-in per run | Free Models fail in groups: Gemini's Flash Models returned 503 together for hours, and a default died within 6 weeks | One default per Provider; automatic Model choice from live lists | A dead Model is replaced by a release until settings exist; evals must run without fallback so scores never mix Models |
| A daily quota ends the run with a retry time | Retrying a per-day 429 wastes minutes and still fails | Retrying every 429 alike | The job runner must turn it into a pause |
| Labels checked by a blind second labeller and an adjudicator | The principal wants minimal oversight, and a check that starts from the draft anchors on it | The principal checks every label | The checker is also an LLM; agreement between two blind sets is the evidence, plus an optional 10-item spot check |

## Evidence

**Method.** Twelve corpus articles, five over 15k characters. For each, Claude agents (never the Models under test) drafted the key Entities, Events and Relationships, each with its sentence, and the principal checks them, asking what is missing before what is wrong. Each Model runs three samples per article, and recall is micro-averaged over every labelled item and sample. Matching rules are fixed in `evals/README.md`; a Relationship counts if it joins the right two Entities, in either direction, with a type the label accepts. Prompt changes were tuned on 4 articles and judged on the other 8. Labels and runs stay local, since they name real people.

**Results**, `gemini-flash-lite-latest`, 12 articles, labels drafted and not yet adjudicated:

| Recall of key items | Bar | First prompt | Current prompt, all 12 | Current, 8 held out |
|---|---|---|---|---|
| Entities | 0.8 | 0.86 | 0.89 | 0.91 |
| Events | 0.6 | 0.25 | 0.62 | 0.63 |
| Relationships | 0.6 (provisional) | 0.30 | 0.45 | 0.40 |

**Adjudicated labels, first half** (6 articles, Flash-Lite pooled over two sets of 36 samples taken six weeks apart): Entities 0.93, Events 0.56, Relationships 0.56. The drafts of the same 6 articles gave 0.48 for Relationships: the drafters over-included links, and the adjudicator cut the non-key ones. The two blind labellings agreed on most Entities and diverged most on Relationships (adjudicator A logged 137 decisions on 6 articles).

**Second Model.** OpenRouter's free `nvidia/nemotron-3.5-super-vl` and `nemotron-3-ultra-550b` each completed both passes on a 28k-character article (374 s and 572 s, against about 25 s for Flash-Lite). Its full eval is limited to 50 requests a day and is part run.

**What moved the numbers.**

- **Grounding.** Our fetch left a space before the punctuation after each link. Models quote the sentence cleanly, so the strict matcher dropped up to half of one article's Entities. The matching rule now ignores case, end ellipses and whitespace that is not between two words, and still admits no fabricated sentence: all 232 sentences of four live replies are found, against 202 before. The fetch was fixed too.
- **Prompt.** The first prompt asked for "fewer, well-grounded items" and had no trade type, so supply chains came out as location links. Asking for everything stated, listing what counts as an Event, and adding `SUPPLIED` doubled Event recall on articles the change was not tuned on. The cost is review load: about 51 Proposals per article before, 76 now.
- **Length.** Long articles recall fewer links (0.34 against 0.54 for short ones). Splitting Pass 2 into parts did not help (0.34 to 0.36), so the gap is not context length.
- **Noise.** Two independent sets of 3 samples gave Event recall of 0.67 and 0.48 on the same articles, and a full rerun six weeks later moved Events from 0.62 to 0.52 on the same labels. Differences under about 0.15 are not signal at this size, so the gate pools both sets of runs.

**Engineering checks.** 522 worker tests and 46 Rust tests run in CI with locked builds; a test-isolation job proves no test touches real data. Each rule of the matcher and the scoring was broken on purpose to confirm a test catches it.

## Risks and open questions

- **Relationship recall is below its provisional bar.** About 0.56 on adjudicated labels so far, against 0.6. Next: adjudicate the second half, then the principal chooses one of three: re-set the bar with a stated reason, try one more Pass 2 change (long articles are the gap), or stop and re-plan (the kill switch).
- **A labelling rule decides about 10 links.** Is a sanction a person `FLAGGED_BY` an agency, or both linked to a sanction Event? The labels currently credit only the second. It is settled in the labelling guide before the second half is adjudicated.
- **Free Models rot.** The OpenRouter default chosen on 2026-10-02 was gone by 2026-11-18. `-latest` aliases hide the Model behind them, Gemini Flash's free tier allows 20 requests a day, and OpenRouter gates some Models per app with a 403. Mitigation: the fallback list and plain failure messages. Refreshing the list takes a release until settings exist.
- **The Operator's keys are untested.** Account state sank Specter. His install worked, and a self-check message after `v0.3.0` runs a real-sized extraction on each of his Providers.
- **Review willingness is untested.** Flash-Lite proposes about 76 items per article, and Super-VL about 165 on a long one. His self-check asks whether he would review such a list.
- **Second Model unproven at scale.** The free tier's 50 requests a day stretch a full eval over three days.
- **Label quality.** Both labellers and the adjudicator are LLMs. Agreement between two blind sets, and an optional 10-decision spot check, are the evidence.
- **Pre-1.0 store.** Pinned exactly; rebuild from files on any format change; SQLite is the fallback.

## Likely challenges

- **Why not a hosted web app?** One non-technical user, sensitive research and no budget. A local app removes auth, hosting and shared-state failures, and the install path is tested on his own Mac before the build continues.
- **Why trust an LLM with intelligence data?** It never writes Knowledge. Every Proposal carries a sentence the code has found verbatim in the article, and only an approval moves it into Knowledge.
- **Why free Models?** The user pays nothing, and the design assumes slow, rate-limited calls. The cost shows up as outages and quotas, handled by retries and fallback.
- **How do you know the eval is not gamed?** The labellers are never a Model under test, a second labelling is done blind to the first, and the prompt was judged on articles it was not tuned on: held-out Event recall went from 0.31 to 0.63, Relationship recall from 0.26 to 0.40. The matching rules are written down, and the two added after the first results both remove credit rather than add it.
- **Why Rust and Python?** Rust holds the invariants and the only handle on the store; Python is where model tooling lives. The boundary is a subprocess speaking JSON Lines, with fixtures both sides test against.
- **What if mnestic breaks?** The files are the truth. Rebuild the index, or move it to SQLite without touching the data.

## Schedule

Reference class: Specter overran its plan by at least 4x and never finished. The estimate applies 2x to the raw sessions left for the 50% date and 3x for the 80% date, in a Monte Carlo over the stated capacity. Re-fit 2026-11-18 with 13 to 17 sessions left at 7 a week: 50% on 2026-12-26, 80% on 2027-01-11, a 63% chance of v1 by 2026-12-31. Three proposed cuts (Model settings, the diagnostics zip, folding the hardening session) raise that to 81%. The first plan slipped six weeks while work waited on people, not on sessions; minimal oversight removes most of those waits.

## Session log

- **2026-10-02, foundations.** One crate, the worker package, the web shell and CI with locked builds, green on its first run.
- **2026-10-03, tracer to the Mac.** Stub page with key entry and a live test extraction of a long article; macOS release; installer and login agent (`v0.2.0`). The pairing call is pending.
- **2026-10-07 to 09, worker.** Two passes with Events, per-item validation, verbatim sentences with offsets, output budget from each Model's spec, truncation reported as such. Quota pause and fallback wait on the pairing call.
- **2026-10-09, quality gate, first half.** Matcher and fetch fixes, the eval with 12 drafted labels, a prompt round with `SUPPLIED`, chunking settled as no. Waiting on the label check and the gate call.
- **2026-11-18, quality gate, second half (part), and worker finish.** The Operator installed on his own. Labels went to a blind second labelling with an adjudicator, and half are adjudicated. The OpenRouter default was replaced after it vanished, and Gemini Flash dropped out. Daily quota, outage retries, gated-Model messages, fallback across families and a total call deadline were added. Version 0.3.0.
