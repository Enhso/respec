# Respec

The language of Respec: an operator turns articles into a sourced network of people, organisations and their links, approving each machine proposal before it counts. A term enters here only after it has caused a confusion.

## Language

### The firewall

**Knowledge / Hypothesis**:
Knowledge is what an operator approved, and every item cites a source. Hypothesis is everything machine-proposed or unresolved.
_Avoid_: verified / unverified, confirmed, true / false

**Proposal**:
One machine-suggested Entity or Relationship from one Document, with its supporting sentence, waiting in the review queue. A Proposal is Hypothesis until an operator approves it.
_Avoid_: suggestion, candidate (a candidate is an existing entity offered as a match)

**Candidate**:
An existing Knowledge entity offered as a possible match for an entity Proposal. Merging the Proposal into it keeps the Proposal's name as an alias.
_Avoid_: match, duplicate

### The network

**Entity**:
A person, organisation, place, vessel or Event named in a Document.
_Avoid_: node

**Event**:
A dated happening with a place, such as a meeting, a transfer or an arrest. It is a kind of Entity, and its participants link to it through Relationships.
_Avoid_: "event" for the worker's progress messages

**Relationship**:
A typed link between two Entities, such as "director of". It holds one or more Observations.
_Avoid_: edge

**Observation**:
One dated report of a Relationship by one Document, with its verbatim sentence.
_Avoid_: edge (a Relationship holds many Observations)

### Sources

**Document / Document text**:
A Document is one ingested source: a fetched article or pasted text with a title or URL and a publication date. Its Document text is the body saved at ingest and never re-fetched.
_Avoid_: description (Specter stuffed article bodies into `Document.description`)

### People and services

**Operator**:
The one person who runs and reviews a Respec instance on their own machine.
_Avoid_: user, admin, analyst

**Provider / Model**:
A Provider is the service holding the key (OpenRouter, Google Gemini). A Model is one entry in a Provider's list.
_Avoid_: using a bare model string as if it named the Provider (Specter's missing prefixes caused five weeks of transport errors)

On Gemini, "free" is a per-project quota, not a property of the Model; only a live call tells the truth.

## Relationships

- A Proposal becomes Knowledge only through an approval, merges included.
- Approving a Relationship that already exists in Knowledge adds an Observation to it, never a second Relationship.
- A Knowledge Relationship never has a Hypothesis endpoint.
