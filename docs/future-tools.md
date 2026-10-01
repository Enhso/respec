# Tools to consider after v1

Shortlist from a survey of [tools.osintnewsletter.com](https://tools.osintnewsletter.com/) (2026-10-01). None of these are in v1 scope; see `ISA.md` Out of Scope.

| Use in Respec | Tool | Why it fits | Integration |
|---------------|------|-------------|-------------|
| Preserve the cited page at ingest | [Wayback Machine](https://web.archive.org/) | Save Page Now keeps a citation alive after link rot or takedown | Free; API not stated on the page (Save Page Now has one) |
| Enrich Vessel and aircraft entities | [OpenSky Network](https://opensky-network.org/) | Lookup by registration, ICAO24, callsign | Free with account; has an API; history restricted |
| Enrich Vessel entities | [Maritime Database](https://maritime-database.com/) | IMO, MMSI, flag, owner and operator | Free core; manual; accuracy disclaimed |
| Flag sanctioned entities | [OFAC Sanctions List Search](https://sanctionssearch.ofac.treas.gov/) | Covers people, companies, vessels and aircraft, the same types Respec extracts | Free; US only; manual |
| Flag sanctioned entities, multi-list | [Sanctions Atlas](https://sanctionsatlas.com/), [dilisense](https://dilisense.com/en) | OFAC, UK, EU, UN and more in one place | Web; dilisense API is paid |
| Corporate registry data | [OpenCorporates](https://opencorporates.com/) | Directors and ownership | API paid; no Russia coverage |
| Export the graph | [OSINTracker](https://www.osintracker.com/) | Local, browser-based link-analysis tool with JSON/CSV import | Free; local |
| Telegram search (not monitoring) | [Deaddrop](https://deaddrop.theosintconsultants.com/login), [Telegago](https://cse.google.com/cse?cx=006368593537057042503:efxu7xprihg) | Find channel posts naming an entity | Deaddrop API is enterprise-only |
| Russian translation | [DeepL](https://www.deepl.com/en/translator) | Handles documents as well as text | Free with caps |

**Gaps in the directory worth checking separately:** a Russian company registry, OpenSanctions, OCCRP Aleph, and any PDF or OCR extraction tool.
