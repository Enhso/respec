# Gate A evals

Gate A measures how much of what matters in an article a free Model proposes (PLAN S4, ISC-42, 43, 43.1 and 62). The reference is a set of labels: for each eval article, its key Entities, key Events and key Relationships, drafted by a Claude agent and checked by the principal.

Labels and runs stay on this machine (`.gitignore`). Labels name real people and the links articles assert between them, and this repo is public. This guide and the eval code are committed.

## Layout

- `labels/<corpus id>.json`: one label file per eval article. The body is `corpus/bodies/<corpus id>.txt`.
- `runs/<prompt hash>/<model>/<corpus id>/s<n>.jsonl`: one cached `extract` run, its progress messages as the worker emitted them, and `log.jsonl` beside it for every attempt. Scoring reads these, so correcting a label never costs a Model call. The prompt hash covers the three prompt files, so a changed prompt never reuses old runs.
- `articles.txt`: the eval set. `dev.txt`: the four articles prompt changes may be tuned on; the other eight are held out, so a prompt fitted to the eval shows up as a gap between the two.

## Commands

Run from `python/`:

```
uv run --frozen python -m respec_worker.evals lint            # check every label file
uv run --frozen --env-file ../.env python -m respec_worker.evals run --provider gemini --model gemini-flash-lite-latest
uv run --frozen python -m respec_worker.evals score           # recall per Model
uv run --frozen python -m respec_worker.evals score --articles ../evals/dev.txt
uv run --frozen python -m respec_worker.evals review          # pages for checking labels
```

## Label file

```json
{
  "article": "<corpus id>",
  "checked": false,
  "entities": [
    {
      "id": "k1",
      "kind": "Person",
      "name": "Viktor Arlenko",
      "aliases": ["Arlenko", "V. Arlenko"],
      "sentences": ["<verbatim sentence from the body>"]
    },
    {
      "id": "k4",
      "kind": "Event",
      "name": "Arrest of Viktor Arlenko",
      "aliases": ["Arlenko's arrest", "Arlenko arrest"],
      "date": "2025-03",
      "sentences": ["<verbatim sentence that reports the arrest>"]
    }
  ],
  "relationships": [
    {
      "from": "k1",
      "to": "k2",
      "types": ["WORKS_FOR", "MEMBER_OF"],
      "sentence": "<verbatim sentence that asserts the link>"
    }
  ],
  "notes": ["<a doubt for the check>"]
}
```

- `kind` is one of `Person`, `Organization`, `Identity` (an alias, persona, account or front someone uses), `Vessel`, `Location` or `Event`, as in the Pass 1 prompt.
- `name` is the fullest form the article gives. `aliases` lists the other ways the article refers to the same Entity, plus obvious standard forms: the surname alone, the acronym and its expansion, the company name with and without its legal form ("UAB Varnas", "Varnas"), transliteration variants the article uses. Leave out any form that could name another Entity in the article.
- An Event's `aliases` are the short noun phrases a reader would use for it. Its `date` is as precise as the article states (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`), or null. A date the body gives without a year ("on June 4") is null: the year is never taken from the publication date.
- `sentences` and `sentence` are copied character for character from the body file; `lint` fails otherwise. One or two per Entity, one to three per Event (the sentences that report it happening), one per Relationship.
- `types` lists every Relationship type a fair reviewer would accept for the link, usually one or two. The types and their meanings are in `python/src/respec_worker/prompts/system.md`. A participant links to an Event with `PARTICIPATED_IN`, from the participant to the Event.
- A sanction, arrest, seizure or expulsion the article reports as an act is a key Event. The sanctioned or arrested party and the acting body each link to it with `PARTICIPATED_IN`, and no direct `FLAGGED_BY` is added for the same fact. A standing sanction the article states without reporting the act ("had been under sanctions since 2025") is a direct `FLAGGED_BY` to the body that imposed it. The scorer accepts only these shapes, because the Models write them: over 88 samples of two Models (ISA Decisions, 2026-11-19), every reported sanction or arrest a Model linked at all came as `PARTICIPATED_IN` to an Event, and a direct `FLAGGED_BY` stood for an act once.
- `notes` is optional: the drafter's doubts for the check, such as an Entity left out as not key, or a link the article only implies.
- `checked` becomes true only after adjudication (see Checking labels).

## What is key

Key means the operator, tracking networks across articles, would want it in his graph after reading this article. It is what the article's central findings are about, not everything it mentions.

- **Key Entities**: the people, organisations, fronts, vessels and specific places the article investigates, or that do the things it reports. These include companies in a supply chain and agencies that sanction, arrest or expel someone. Leave out passing mentions, background countries, quoted experts who only comment, and the outlet and its journalists unless they are part of the story. A country is key only when it acts in a key Relationship and no more specific body is named. Expect 3 to 8 for short news, 8 to 20 for a long investigation.
- **Key Events**: specific happenings the article reports, such as an arrest, a sanction, a shipment, an attack, a meeting, a sentence or an expulsion. Trends and states of affairs are not Events. Expect 0 to 2 for short news, up to 5 for a long article.
- **Key Relationships**: links between two key Entities that the article asserts as part of its findings. Expect 2 to 8 for short news, 5 to 20 for a long article.

## Scoring

Each Model runs three samples per article. Recall is micro-averaged over every labelled item and every successful sample, and printed per article and for articles over and under 15k characters (the chunking question).

- A proposed Entity recalls a key Entity when its name equals the key Entity's name or an alias, ignoring case, accents, punctuation and a leading "the". It also counts when the name contains one of those forms as whole words and the kinds fit: the same kind, or `Identity` with `Person` or `Organization`. Only the closest names count: an exact name beats a contained one, and a longer contained form beats a shorter one, so a subsidiary named after its parent does not also recall the parent.
- A proposed Event recalls a key Event when its name matches as above, or its sentence overlaps one of the key Event's sentences in the Document text. A name match wins: an Event named as one key Event is not also credited with another that shares its sentence.
- A proposed Relationship recalls a key Relationship when its two ends recall the key Relationship's two ends, in either direction, and its type is one of `types`. Recall with any type is printed beside it, to tell a missed link from a mistyped one.

## Checking labels

Labels are checked by a blind second labelling, not by hand (Decisions, 2026-11-18). A second Claude agent labels each article from this guide and the body alone, without seeing the first set. An adjudicator then gets both sets as "set 1" and "set 2", in random order, and settles every item they disagree on by "What is key". An item both sets have is kept unless it is plainly wrong. The adjudicator writes the final file with `checked: true` and records each decision with a one-line reason. The first draft and the blind set stay beside the final labels, in `labels-draft/` and `labels-blind/`.

To read one article's labels against its text, open the review page (`uv run --frozen python -m respec_worker.evals review`, then the HTML file it names). Ask first what is missing, then what is wrong: missing items cost recall that no Model can earn.
