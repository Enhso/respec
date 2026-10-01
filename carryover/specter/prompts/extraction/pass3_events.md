PASS: 3

Article body follows between the markers. The Pass-1 entities and the
Pass-2 relationships flagged for event promotion follow as JSON.

Aggregate the flagged tuples into Event proposals per the Pass 3
schema described in the system prompt. Reference entities by their
Pass-1 uuids. Reply with one JSON document and nothing else.

<<<PASS 1 ENTITIES>>>
{pass1_entities_json}
<<<END PASS 1 ENTITIES>>>

<<<EVENT CANDIDATES>>>
{event_candidates_json}
<<<END EVENT CANDIDATES>>>

<<<BODY>>>
{body}
<<<END BODY>>>
