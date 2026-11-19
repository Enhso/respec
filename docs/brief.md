# Respec brief

As of 2026-11-19. Rewritten every session; the spec is `ISA.md`, the order of work `PLAN.md`.

## What it is

Respec turns long investigative articles (The Insider, Bellingcat) into a sourced graph of people, organisations, vessels, places and Events. A free LLM proposes Entities and Relationships, each tied to a verbatim sentence of the article. The Operator approves, edits or rejects each one, and only approved items become Knowledge. It runs as one local instance per Operator on his own Mac, on free model tiers.

The first Operator is a researcher who tracks Russian state-adjacent networks and is not at home in a terminal. v1 is done when five real articles go end to end through the app on free Models, and he ingests and reviews an article we did not prepare, unsupervised, within a week. The target is 2026-12-31.

Respec restarts Specter, which never ran end to end: 29 recorded extraction runs, 0 successes. Its failures came from provider account state and a 4,096-token output cap, and its user was never set up on it. Respec's order of work is built to hit those failure points first.

## Where it stands

- **Built:** CI with locked builds; the Rust server shell with key storage and Host and Origin checks; the macOS release, the one-command installer and a login agent; and the extraction worker. The worker makes two passes, grounds every item in a verbatim sentence, sizes its output budget from each Model's spec, and detects truncation. It retries per-minute limits and outages, stops at once on a daily quota and says when to retry, and with `--fallback` moves a failing pass to the next Model in a list that spans two Model families and now starts with Super-VL. Also built: the quality eval with its 12 adjudicated label files. Version 0.3.0.
- **Not built:** the store, the job runner and Documents API, the review queue, entity matching, navigation, the settings page.
- **Quality gate: passed, conditionally.** The principal chose OpenRouter's free `nvidia/nemotron-3.5-super-vl` as the extraction Model on 7 of 12 articles (see Evidence). The gate closes when its run covers the other 5 and all three bars still hold; the free tier's daily cap makes that one or two more days. Next milestone: the store.
- **Unverified on the Operator's Mac:** he installed `v0.2.0` himself without problems, but a real-sized extraction on his own keys has not run. `v0.3.0` is released, and a self-check message (both Providers, a reboot, and whether he would review such a list) is with the principal to forward.

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
| Super-VL extracts, Flash-Lite is its fallback | It clears all three recall bars where Flash-Lite misses two (Events 0.91 against 0.56, Relationships 0.69 against 0.47, same 7 articles) | Flash-Lite with the Relationships bar lowered to 0.5 | 5 to 6 minutes per article against 20 s; about twice the list to review; about 20 articles a day on the free cap; 1 attempt in 6 fails |
| A reported sanction or arrest is an Event | Both Models write it that way: in 88 samples a direct `FLAGGED_BY` stood for an act once | Crediting either shape in the scorer | A Model that writes the direct link scores nothing for it |
| A daily quota ends the run with a retry time | Retrying a per-day 429 wastes minutes and still fails | Retrying every 429 alike | The job runner must turn it into a pause |
| Labels checked by a blind second labeller and an adjudicator | The principal wants minimal oversight, and a check that starts from the draft anchors on it | The principal checks every label | The checker is also an LLM; agreement between two blind sets is the evidence, plus an optional 10-item spot check |

## Evidence

**Method.** Twelve corpus articles, five over 15k characters. For each, a Claude agent (never a Model under test) drafted the key Entities, Events and Relationships, each with its sentence. A second agent labelled every article again without seeing the draft, and an adjudicator who could not tell the sets apart settled each disagreement: 237 of them across 12 articles, each logged with a one-line reason. Each Model runs three samples per article, and recall is micro-averaged over every labelled item and sample. Matching rules are fixed in `evals/README.md`; a Relationship counts if it joins the right two Entities, in either direction, with a type the label accepts. Prompt changes were tuned on 4 articles and judged on the other 8. Labels and runs stay local, since they name real people.

**Results** on adjudicated labels, current prompt. Flash-Lite is pooled over two sets of 3 samples per article taken six weeks apart; Super-VL has 19 samples over the 7 articles its free quota has reached so far.

| Recall of key items | Bar | Flash-Lite, all 12 | Flash-Lite, the same 7 | Super-VL, 7 articles | Super-VL, 5 held out |
|---|---|---|---|---|---|
| Entities | 0.8 | 0.91 | 0.91 | 0.89 | 0.85 |
| Events | 0.6 | 0.57 | 0.56 | 0.91 | 0.89 |
| Relationships | 0.6 (provisional) | 0.50 | 0.47 | 0.69 | 0.62 |

Flash-Lite on its 8 held-out articles: 0.94, 0.59 and 0.45. The prompt was tuned on Flash-Lite, so Super-VL's held-out numbers are the cleaner test. Super-VL is slower and more prolific: a median 337 s per article against 17 s, about 65 Entities and 61 Relationships per article against 48 and 32, and 23% of its proposed links recall a key link against 30% for Flash-Lite. Gate A measures recall only, so part of its lead is volume; review cost is measured at gate B. It failed 4 of 23 attempts (3 `bad_output`, 1 `other`).

**Adjudication** raised Flash-Lite's Relationship recall from 0.44 on the drafts to 0.50, mostly by cutting links the drafters over-included. The two blind sets agreed on most Entities and diverged most on Relationships.

**What moved the numbers.**

- **Grounding.** Our fetch left a space before the punctuation after each link. Models quote the sentence cleanly, so the strict matcher dropped up to half of one article's Entities. The matching rule now ignores case, end ellipses and whitespace that is not between two words, and still admits no fabricated sentence: all 232 sentences of four live replies are found, against 202 before. The fetch was fixed too.
- **Prompt.** The first prompt asked for "fewer, well-grounded items" and had no trade type, so supply chains came out as location links. Asking for everything stated, listing what counts as an Event, and adding `SUPPLIED` doubled Event recall on articles the change was not tuned on. The cost is review load: about 51 Proposals per article before, 76 now.
- **Length.** Long articles recall fewer links: 0.40 against 0.57 for Flash-Lite, 0.64 against 0.86 for Super-VL. Splitting Pass 2 into parts did not help Flash-Lite (0.34 to 0.36 on drafted labels), so the gap is not context length.
- **Noise.** A full Flash-Lite rerun six weeks later moved Events from 0.62 to 0.53 on the same adjudicated labels. Differences under about 0.15 are not signal at this size, so Flash-Lite's numbers pool both sets of runs. Super-VL's leads (0.35 on Events, 0.22 on Relationships) are past that band.

**Engineering checks.** 522 worker tests and 46 Rust tests run in CI with locked builds; a test-isolation job proves no test touches real data. Each rule of the matcher and the scoring was broken on purpose to confirm a test catches it.

## Risks and open questions

- **The gate rests on 7 of 12 articles.** Super-VL's numbers come from 19 samples; its weakest article so far (a 28k-character one) recalls 0.34 of key links. Its run resumes each day the free quota resets. If a bar falls once all 12 are in, the verdict goes back to the principal, with a 0.5 Relationships bar as the fallback position.
- **The chosen Model is a free OpenRouter Model, and those rot.** The OpenRouter default chosen on 2026-10-02 was gone by 2026-11-18. If Super-VL goes, extraction falls back to Flash-Lite at 0.57 and 0.50 recall. The principal kept the settings session whole (live Model lists, switching, a test button), so a replacement will not need a release after v1's settings land.
- **Slow and sometimes failing.** 5 to 6 minutes per article, and 1 attempt in 6 fails after minutes of waiting before the fallback takes over. The job runner must show progress and resume; the worker's 600 s call deadline has not been hit (slowest two-pass run: 639 s).
- **The Operator's keys are untested.** Account state sank Specter. His install worked, and a self-check message after `v0.3.0` runs a real-sized extraction on each of his Providers.
- **Review willingness is untested.** Super-VL proposes about 125 items per article, and up to 170 on a long one. His self-check asks whether he would review such a list, and gate B measures it.
- **Label quality.** Both labellers and the adjudicator are LLMs. Agreement between two blind sets, and an optional 10-decision spot check, are the evidence.
- **Pre-1.0 store.** Pinned exactly; rebuild from files on any format change; SQLite is the fallback.

## Likely challenges

- **Why not a hosted web app?** One non-technical user, sensitive research and no budget. A local app removes auth, hosting and shared-state failures, and the install path is tested on his own Mac before the build continues.
- **Why trust an LLM with intelligence data?** It never writes Knowledge. Every Proposal carries a sentence the code has found verbatim in the article, and only an approval moves it into Knowledge.
- **Doesn't the Model that proposes more simply win on recall?** Partly. 23% of Super-VL's links recall a key link against 30% of Flash-Lite's, so its list is longer and a little thinner. But it finds 14 of about 20 key links per article where Flash-Lite finds 10, and no amount of review recovers a link that was never proposed. Review cost is measured at gate B, on real use.
- **Why free Models?** The user pays nothing, and the design assumes slow, rate-limited calls. The cost shows up as outages and quotas, handled by retries and fallback.
- **How do you know the eval is not gamed?** The labellers are never a Model under test, a second labelling is done blind to the first, and the prompt was judged on articles it was not tuned on. It was tuned on Flash-Lite, and Super-VL, never tuned on, clears every bar on the 5 held-out articles it has run so far. The matching rules are written down, and the two added after the first results both remove credit rather than add it.
- **Why Rust and Python?** Rust holds the invariants and the only handle on the store; Python is where model tooling lives. The boundary is a subprocess speaking JSON Lines, with fixtures both sides test against.
- **What if mnestic breaks?** The files are the truth. Rebuild the index, or move it to SQLite without touching the data.

## Schedule

Reference class: Specter overran its plan by at least 4x and never finished. The estimate applies 2x to the raw sessions left for the 50% date and 3x for the 80% date, in a Monte Carlo over the stated capacity. Re-fit 2026-11-18 with 13 to 17 sessions left at 7 a week: 50% on 2026-12-26, 80% on 2027-01-11, a 63% chance of v1 by 2026-12-31. Three proposed cuts (Model settings, the diagnostics zip, folding the hardening session) would have raised that to 81%; the principal declined them on 2026-11-19, so 63% stands. The first plan slipped six weeks while work waited on people, not on sessions; minimal oversight removes most of those waits.

## Session log

- **2026-10-02, foundations.** One crate, the worker package, the web shell and CI with locked builds, green on its first run.
- **2026-10-03, tracer to the Mac.** Stub page with key entry and a live test extraction of a long article; macOS release; installer and login agent (`v0.2.0`). The pairing call is pending.
- **2026-10-07 to 09, worker.** Two passes with Events, per-item validation, verbatim sentences with offsets, output budget from each Model's spec, truncation reported as such. Quota pause and fallback wait on the pairing call.
- **2026-10-09, quality gate, first half.** Matcher and fetch fixes, the eval with 12 drafted labels, a prompt round with `SUPPLIED`, chunking settled as no. Waiting on the label check and the gate call.
- **2026-11-18, quality gate, second half (part), and worker finish.** The Operator installed on his own. Labels went to a blind second labelling with an adjudicator, and half are adjudicated. The OpenRouter default was replaced after it vanished, and Gemini Flash dropped out. Daily quota, outage retries, gated-Model messages, fallback across families and a total call deadline were added. Version 0.3.0.
- **2026-11-19, quality gate decided.** Sanctions and arrests labelled as Events, after checking how both Models write them. The second half adjudicated, so all 12 label files are checked. Super-VL chosen over Flash-Lite on 7 of 12 articles, conditional on the rest; the fallback list now starts with it. No scope cuts.
