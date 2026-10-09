# Writing the brief

`docs/brief.md` is Respec's defence brief: a reader who owns the project's technical decisions can defend them cold, without having followed the sessions. They read code and know the common stack; they need what exists, why it is built this way, what each choice costs, how we know it works, and what could still sink it.

## When

Rewrite it at the end of every session, in the same commit as the PLAN.md Now rewrite. The session is done when every number in the brief matches the latest ISA and eval output, and every decision the session took appears in it.

## Sections, in this order

1. **What it is**: the product in three sentences, who it is for, and the v1 exit criterion.
2. **Where it stands**: what is built, what is not, and the next milestone.
3. **Architecture**: the components and the boundary between them, each with the reason for it.
4. **Decisions**: a table of choice, reason, rejected alternative, and cost.
5. **Evidence**: how quality is measured, the current numbers, and what they rest on.
6. **Risks and open questions**: what could still sink it, each with its mitigation or the step that settles it. This section always has entries.
7. **Likely challenges**: the hard questions a technical peer would ask, each answered in two or three sentences.
8. **Schedule**: the estimate, the method behind it, and the dates.
9. **Session log**: one line per session, newest last.

## Rules

- Every claim carries its evidence: a number with its conditions (Model, samples, whether the labels were checked), a test, or a commit. A claim not yet verified says so.
- Every decision names the alternative it beat and what it costs.
- Plain technical prose: mechanisms, numbers and tradeoffs. Common technology needs no explanation; Respec's own terms come from `CONTEXT.md`, and internal shorthand (claim ids, session numbers) is spelled out on first use or left out.
- About two pages: under 200 lines. Rewrite stale content in place, and keep history only in the session log.
- Names in examples are invented, as everywhere in this public repo.
