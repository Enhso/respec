---
status: accepted
---

# Events are a kind of Entity, extracted in two passes

Pass 1 extracts Events alongside other Entities, with their date and place, and pass 2 links participants to them through Relationships. There is no third pass. Specter's separate events pass produced no Events in production (both stored Events were hand-entered), and treating Events as Entities lets them reuse entity matching.

## Consequences

Pass-1 output grows on long articles. Gate A measures event recall (ISC-62): below 0.6, Events are dropped or a third pass returns, with a Decisions row either way.
