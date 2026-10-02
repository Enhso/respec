---
status: accepted
---

# One local instance per operator, no shared data

Each operator runs their own Respec on their own Mac, bound to 127.0.0.1, with no sync, no shared database, no accounts and no auth. Specter's shared cloud database was its main source of fragility, and the intended operator keeps his research to himself. Loopback alone does not stop DNS rebinding or cross-site requests, so the server also rejects foreign Host and Origin headers (ISC-46, 47).

## Considered Options

- **A shared cloud database** (Specter): fragile, and it mixes two operators' research.
- **A hosted single instance**: needs auth and a public surface, and moves the operator's data off his machine.
