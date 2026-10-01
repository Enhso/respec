PASS: 2

Article body follows between the markers. Pass 1 entities (with their
orchestrator-stamped uuids) follow as JSON.

Extract dyadic relationships per the Pass 2 schema described in the
system prompt. Reference entities by their Pass-1 uuids. Set the
`event_candidate` / `event_candidate_reason` flag per the §7
event-promotion rule. Reply with one JSON document and nothing else.

<<<PASS 1 ENTITIES>>>
{pass1_entities_json}
<<<END PASS 1 ENTITIES>>>

<<<BODY>>>
{body}
<<<END BODY>>>
