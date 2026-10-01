# Respec

A local web app for mapping networks from open-source reporting. Paste an article; a free LLM proposes the people, organisations and relationships in it, each with the sentence it came from; you approve what holds. Approved items form a Knowledge graph where every link cites its sources. Machine proposals stay in Hypothesis until a human promotes them.

Respec is the from-scratch successor to Specter. Rust owns the embedded mnestic store, the API and the UI server; a stateless Python worker fetches articles and calls models.

Status: pre-alpha, nothing runs yet. The spec, decisions and open questions are in [`ISA.md`](ISA.md).
