"""Both passes: the prompt files, and parsing the Model's replies."""

import logging

import orjson
import pytest

from respec_worker.extraction import (
    PASS1_MAX_TOKENS,
    BadOutput,
    DocumentText,
    Grounded,
    Pass1EntityProposal,
    Pass2RelationshipProposal,
    ground,
    known_relationships,
    load_prompt,
    parse_pass1,
    parse_pass2,
    pass1_messages,
    pass2_messages,
    stamp_ids,
)

ONE_ENTITY = (
    '{"entities": [{"label": "Person", "name": "Ada Verrin", '
    '"supporting_sentences": ["Ada Verrin signed the order."]}]}'
)


def test_the_prompt_files_load_from_the_package() -> None:
    """The prompts are package data, not read from the working directory."""
    system = load_prompt("system.md")
    assert "Pass 1 — Entities" in system
    assert "Pass 2 — Relationships" in system
    assert "{body}" in load_prompt("pass1_entities.md")
    pass2 = load_prompt("pass2_relationships.md")
    assert "{body}" in pass2 and "{entities}" in pass2


def test_the_prompts_describe_two_passes_and_no_event_promotion() -> None:
    """Specter's three-pass framing and its event-promotion rule are gone."""
    prompts = "".join(
        load_prompt(name)
        for name in ("system.md", "pass1_entities.md", "pass2_relationships.md")
    )

    for retired in (
        "Pass 3",
        "PASS: 3",
        "event_candidate",
        "promotion",
        "uuid",
        "INVOLVED",
    ):
        assert retired not in prompts


def test_pass1_messages_are_the_system_prompt_then_the_filled_template() -> None:
    """The user message is the template with the Document text in its markers."""
    system, user = pass1_messages("THE DOCUMENT TEXT")

    assert system == {"role": "system", "content": load_prompt("system.md")}
    assert user["role"] == "user"
    assert user["content"].startswith("PASS: 1")
    assert "<<<BODY>>>\nTHE DOCUMENT TEXT\n<<<END BODY>>>" in user["content"]
    assert "{body}" not in user["content"]


def test_a_placeholder_inside_the_document_text_is_left_alone() -> None:
    """Text such as `{body}` or a brace in an article is not template syntax."""
    _, user = pass1_messages("a {body} and a {stray} brace")

    assert "a {body} and a {stray} brace" in user["content"]


def test_the_output_budget_is_the_16k_cap() -> None:
    """Specter's 4,096 truncated a long article; this budget does not."""
    assert PASS1_MAX_TOKENS == 16384


@pytest.mark.parametrize(
    "reply",
    [
        ONE_ENTITY,
        f"```json\n{ONE_ENTITY}\n```",
        f"```\n{ONE_ENTITY}\n```",
        f"<json>{ONE_ENTITY}</json>",
        f"Here is the list you asked for:\n{ONE_ENTITY}\nLet me know if you need more.",
        f"Sure!\n```json\n{ONE_ENTITY}\n```\nDone.",
        f"The entities [as asked]:\n```json\n{ONE_ENTITY}\n```",
        f"The entities [as asked]: <json>{ONE_ENTITY}</json>",
    ],
    ids=[
        "bare",
        "fenced-json",
        "fenced-plain",
        "tagged",
        "prose-around",
        "prose-fence",
        "bracket-before-fence",
        "bracket-before-tag",
    ],
)
def test_valid_replies_parse_whatever_wraps_them(reply: str) -> None:
    """Bare, fenced, tagged and prose-wrapped JSON all give the same entity."""
    (entity,) = parse_pass1(reply).entities

    assert entity.label == "Person"
    assert entity.name == "Ada Verrin"
    assert entity.supporting_sentences == ["Ada Verrin signed the order."]
    assert entity.attributes == {}


def test_an_empty_entity_list_is_a_valid_reply() -> None:
    """A Model that finds nothing says so; that is not bad output."""
    assert parse_pass1('{"entities": []}').entities == []


def test_braces_and_quotes_inside_a_string_do_not_end_the_document() -> None:
    """The scanner counts braces outside string literals only."""
    reply = (
        'noise {"entities": [{"label": "Vessel", "name": "MV Lark", '
        '"supporting_sentences": ["She sailed as \\"Lark} {\\" in 2021."]}]} tail }'
    )

    (entity,) = parse_pass1(reply).entities

    assert entity.supporting_sentences == ['She sailed as "Lark} {" in 2021.']


def test_an_id_the_model_made_up_is_dropped_and_attributes_are_kept() -> None:
    """The prompt says not to invent ids; one that arrives is ignored."""
    reply = (
        '{"entities": [{"id": "x-1", "label": "Location", "name": "Karsk", '
        '"supporting_sentences": ["It reached Karsk."], '
        '"attributes": {"type": "city"}}]}'
    )

    (entity,) = parse_pass1(reply).entities

    assert not hasattr(entity, "id")
    assert entity.attributes == {"type": "city"}


def test_extra_sentences_are_clipped_instead_of_failing_the_reply() -> None:
    """A third sentence and an over-long sentence are cut to the schema's caps."""
    long = "x" * 2500
    reply = (
        '{"entities": [{"label": "Person", "name": "A", "supporting_sentences": '
        f'["one", "{long}", "three"]}}]}}'
    )

    (entity,) = parse_pass1(reply).entities

    assert entity.supporting_sentences == ["one", "x" * 2000]


def _entity_json(label: str, name: str, sentences: str) -> str:
    return (
        f'{{"entities": [{{"label": "{label}", "name": "{name}", '
        f'"supporting_sentences": {sentences}}}]}}'
    )


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   \n",
        "I could not find any entities.",
        '{"entities": [{"label": "Person", "name": "Ada Verrin", "supporting',
        '{"entities": [}',
        '{"entities": "none"}',
        '{"entities": {"label": "Person"}}',
        '{"people": []}',
        "[]",
    ],
    ids=[
        "empty",
        "blank",
        "prose-only",
        "cut-off",
        "invalid-json",
        "entities-not-a-list",
        "entities-an-object",
        "wrong-key",
        "top-level-list",
    ],
)
def test_a_reply_that_is_not_valid_pass1_json_is_bad_output(reply: str) -> None:
    """Without complete JSON, or without the `entities` list, the whole reply
    fails: `BadOutput` with a plain sentence that holds none of the reply."""
    with pytest.raises(BadOutput) as excinfo:
        parse_pass1(reply)

    message = excinfo.value.message
    assert message == str(excinfo.value)
    assert message.endswith(".")
    assert "Ada Verrin" not in message and "Rex" not in message


# ---- Events in Pass 1 ----


def _event_json(**fields: object) -> str:
    event = {
        "label": "Event",
        "name": "Arrival of the shipment in Karsk",
        "supporting_sentences": ["The shipment arrived in Karsk in February 2024."],
        **fields,
    }
    return orjson.dumps({"entities": [event]}).decode()


def test_an_event_carries_a_date_and_a_place() -> None:
    """The two Event fields come through as the Model wrote them."""
    (event,) = parse_pass1(_event_json(date="2024-02", place="Karsk")).entities

    assert (event.date, event.place) == ("2024-02", "Karsk")


@pytest.mark.parametrize("date", ["2024", "2024-02", "2024-02-12", "2024-02-29"])
def test_an_event_date_may_be_partial(date: str) -> None:
    """YYYY, YYYY-MM and YYYY-MM-DD all validate, with a real calendar day; the
    date's form is its precision."""
    (event,) = parse_pass1(_event_json(date=date)).entities

    assert event.date == date


def test_an_event_may_leave_its_date_and_place_out_or_null() -> None:
    """The article need not say when or where; omitted and null mean the same."""
    omitted = parse_pass1(_event_json()).entities[0]
    nulls = parse_pass1(_event_json(date=None, place=None)).entities[0]

    assert omitted == nulls
    assert (omitted.date, omitted.place) == (None, None)


def test_a_non_event_with_null_event_fields_is_fine() -> None:
    """A Model that writes `"date": null` on a Person has not given it a date."""
    reply = orjson.dumps(
        {
            "entities": [
                {
                    "label": "Person",
                    "name": "Ada Verrin",
                    "supporting_sentences": ["Ada Verrin signed the order."],
                    "date": None,
                    "place": None,
                }
            ]
        }
    ).decode()

    response = parse_pass1(reply)

    (person,) = response.entities
    assert (person.date, person.place) == (None, None)
    assert response.dropped == 0


# ---- one malformed item (ISC-63) ----


def _entity(name: str = "Ada Verrin", **fields: object) -> dict[str, object]:
    return {
        "label": "Person",
        "name": name,
        "supporting_sentences": [f"{name} signed the order."],
        **fields,
    }


def _entities_reply(*items: object) -> str:
    return orjson.dumps({"entities": list(items)}).decode()


BAD_ENTITIES: dict[str, object] = {
    "unknown-label": _entity(label="Dog"),
    "no-sentence": _entity(supporting_sentences=[]),
    "sentence-not-a-list": _entity(supporting_sentences="Ada signed."),
    "empty-name": _entity(name=""),
    "name-too-long": _entity(name="x" * 201),
    "missing-name": {"label": "Person", "supporting_sentences": ["a"]},
    "attributes-not-an-object": _entity(attributes=["nationality"]),
    "garbled-required-key": {
        "label": "Person",
        "name": "Ada Verrin",
        "\u0436\u0436supporting_sentences": ["Ada Verrin signed the order."],
    },
    "not-an-object": "Ada Verrin",
    "null-item": None,
    "a-list-item": ["Ada Verrin"],
    "person-with-a-date": _entity(date="2024"),
    "organization-with-a-place": _entity(label="Organization", place="Karsk"),
    "event-month-13": _entity(label="Event", date="2024-13"),
    "event-29-feb-common-year": _entity(label="Event", date="2023-02-29"),
    "event-day-30-feb": _entity(label="Event", date="2024-02-30"),
    "event-two-digit-year": _entity(label="Event", date="24-02"),
    "event-one-digit-month": _entity(label="Event", date="2024-2"),
    "event-free-text-date": _entity(label="Event", date="Feb 2024"),
    "event-empty-date": _entity(label="Event", date=""),
    "event-timestamp": _entity(label="Event", date="2024-02-12T09:00"),
    "event-empty-place": _entity(label="Event", place=""),
}


@pytest.mark.parametrize("bad", BAD_ENTITIES.values(), ids=BAD_ENTITIES.keys())
def test_malformed_item_in_pass1_is_dropped_and_the_rest_are_kept(
    bad: object,
) -> None:
    """One bad item among good ones costs only itself, and is counted."""
    reply = _entities_reply(_entity("Ada Verrin"), bad, _entity("MV Lark"))

    response = parse_pass1(reply)

    assert [e.name for e in response.entities] == ["Ada Verrin", "MV Lark"]
    assert response.dropped == 1


def test_malformed_item_an_unknown_key_is_ignored_and_the_item_kept() -> None:
    """A key the schema does not have, such as a leftover `date_precision` or a
    made-up `confidence`, costs nothing: the item is kept and nothing is counted."""
    reply = _entities_reply(
        _entity("Ada Verrin", confidence=0.9, rank=1),
        _entity(
            "Arrival of the shipment",
            label="Event",
            date="2024-02",
            date_precision="month",
            place="Karsk",
        ),
    )

    response = parse_pass1(reply)

    assert [e.name for e in response.entities] == [
        "Ada Verrin",
        "Arrival of the shipment",
    ]
    assert (response.entities[1].date, response.entities[1].place) == (
        "2024-02",
        "Karsk",
    )
    assert response.dropped == 0


def test_malformed_item_a_whitespace_padded_key_is_accepted() -> None:
    """Surrounding whitespace on a key is stripped before the item is checked,
    so `" supporting_sentences"` is the field it was meant to be."""
    padded = {
        " label": "Person",
        "name ": "Ada Verrin",
        "\tsupporting_sentences\n": ["Ada Verrin signed the order."],
        "  attributes": {"nationality": "Veldovan"},
    }

    response = parse_pass1(_entities_reply(padded, _entity("MV Lark")))

    assert [e.name for e in response.entities] == ["Ada Verrin", "MV Lark"]
    assert response.entities[0].supporting_sentences == ["Ada Verrin signed the order."]
    assert response.entities[0].attributes == {"nationality": "Veldovan"}
    assert response.dropped == 0


def test_malformed_item_a_garbled_required_key_still_drops_the_item() -> None:
    """Only whitespace is repaired: a junk prefix on a required key leaves the
    field missing, so the item is dropped and counted once."""
    garbled = {
        "label": "Person",
        "name": "Ada Verrin",
        "\u0436supporting_sentences": ["Ada Verrin signed the order."],
    }

    response = parse_pass1(_entities_reply(_entity("MV Lark"), garbled))

    assert [e.name for e in response.entities] == ["MV Lark"]
    assert response.dropped == 1


def test_malformed_item_every_item_bad_is_an_empty_result_not_bad_output() -> None:
    """The reply had the list, so it did not fail; it just held nothing usable."""
    response = parse_pass1(_entities_reply(*BAD_ENTITIES.values()))

    assert response.entities == []
    assert response.dropped == len(BAD_ENTITIES)


def test_malformed_item_a_clean_reply_drops_nothing() -> None:
    """The count is zero when every item validates."""
    assert parse_pass1(_entities_reply(_entity(), _entity("MV Lark"))).dropped == 0
    assert parse_pass1('{"entities": []}').dropped == 0


def test_malformed_item_is_logged_by_error_type_and_field_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One error line per dropped item, naming the item's place in the list, the
    error types and the field; no name, sentence or key the Model wrote."""
    reply = _entities_reply(
        _entity("Ada Verrin"),
        BAD_ENTITIES["garbled-required-key"],
        _entity("MV Lark", date="2024-13", label="Event", place="Karsk"),
        _entity("Dolmen Freight", label="Dog"),
    )

    with caplog.at_level(logging.ERROR):
        parse_pass1(reply)

    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 3
    lines = [r.getMessage() for r in records]
    assert all("entity" in line for line in lines)
    assert "2" in lines[0] and "missing" in lines[0]
    assert "supporting_sentences" in lines[0]
    assert "date" in lines[1] and "3" in lines[1]
    assert "label" in lines[2] and "literal_error" in lines[2]
    text = " ".join(lines)
    for content in ("Verrin", "Lark", "Dolmen", "Karsk", "signed", "2024-13", "\u0436"):
        assert content not in text


# ---- Pass 2 ----


def _link(**fields: object) -> dict[str, object]:
    return {
        "type": "PARTICIPATED_IN",
        "from_id": "e1",
        "to_id": "e2",
        "date_precision": "unknown",
        "supporting_sentences": ["Ada Verrin attended the meeting."],
        **fields,
    }


def _links(*links: object) -> str:
    return orjson.dumps({"relationships": list(links)}).decode()


@pytest.mark.parametrize(
    "wrap",
    ["{}", "```json\n{}\n```", "<json>{}</json>", "Here you go:\n{}\nAnything else?"],
    ids=["bare", "fenced", "tagged", "prose-around"],
)
def test_a_pass2_reply_parses_whatever_wraps_it(wrap: str) -> None:
    """Pass 2 shares Pass 1's tolerance for how the JSON is wrapped."""
    reply = wrap.format(_links(_link()))

    response = parse_pass2(reply)

    (link,) = response.relationships
    assert (link.type, link.from_id, link.to_id) == ("PARTICIPATED_IN", "e1", "e2")
    assert link.supporting_sentences == ["Ada Verrin attended the meeting."]
    assert (link.date_from, link.date_to, link.date_precision) == (
        None,
        None,
        "unknown",
    )
    assert response.dropped == 0


def test_an_empty_relationship_list_is_a_valid_reply() -> None:
    """An article can hold no relationships between its entities."""
    response = parse_pass2('{"relationships": []}')

    assert response.relationships == [] and response.dropped == 0


def test_a_relationship_may_carry_partial_dates() -> None:
    """Relationship dates use the same partial ISO form as an Event's."""
    reply = _links(_link(date_from="2023", date_to="2024-02", date_precision="range"))

    (link,) = parse_pass2(reply).relationships

    assert (link.date_from, link.date_to) == ("2023", "2024-02")


@pytest.mark.parametrize(
    "type_",
    [
        "WORKS_FOR",
        "OWNS",
        "CONTROLS",
        "MEMBER_OF",
        "ASSOCIATED_WITH",
        "FAMILY_OF",
        "BORN_IN",
        "LOCATED_IN",
        "HEADQUARTERED_IN",
        "OPERATES_IN",
        "TRAVELED_TO",
        "PARTICIPATED_IN",
        "REGISTERED_TO",
        "FLAGGED_BY",
        "DOCUMENTS",
    ],
)
def test_every_extractable_relationship_type_validates(type_: str) -> None:
    """Specter's fifteen extractable types; `PARTICIPATED_IN` links a participant
    to an Event."""
    (link,) = parse_pass2(_links(_link(type=type_))).relationships

    assert link.type == type_


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "I found no relationships.",
        '{"relationships": [{"type": "OWNS", "from_id": "e1", "to_id',
        '{"entities": []}',
        '{"relationships": "none"}',
        '{"relationships": {"type": "OWNS"}}',
        "[]",
    ],
    ids=[
        "empty",
        "prose-only",
        "cut-off",
        "pass-1-shape",
        "not-a-list",
        "an-object",
        "top-level-list",
    ],
)
def test_a_reply_that_is_not_valid_pass2_json_is_bad_output(reply: str) -> None:
    """Without complete JSON, or without the `relationships` list, the whole
    reply fails: `BadOutput` with a plain sentence that holds none of the reply
    and does not talk about entities."""
    with pytest.raises(BadOutput) as excinfo:
        parse_pass2(reply)

    message = excinfo.value.message
    assert message.endswith(".")
    assert "Ada Verrin" not in message
    assert "entity" not in message


BAD_LINKS: dict[str, object] = {
    "structural-type": _link(type="MENTIONS"),
    "retired-type": _link(type="INVOLVED"),
    "lower-case-type": _link(type="owns"),
    "empty-type": _link(type=""),
    "no-sentence": _link(supporting_sentences=[]),
    "self-link": _link(from_id="e1", to_id="e1"),
    "dates-reversed": _link(date_from="2024-02", date_to="2024-01"),
    "impossible-date": _link(date_from="2024-02-31"),
    "unknown-precision": _link(date_precision="week"),
    "no-precision": {k: v for k, v in _link().items() if k != "date_precision"},
    "garbled-required-key": {
        "\u0436type": "OWNS",
        **{k: v for k, v in _link().items() if k != "type"},
    },
    "not-an-object": "e1 owns e2",
    "null-item": None,
}


@pytest.mark.parametrize("bad", BAD_LINKS.values(), ids=BAD_LINKS.keys())
def test_malformed_item_in_pass2_is_dropped_and_the_rest_are_kept(bad: object) -> None:
    """One bad relationship among good ones costs only itself, and is counted."""
    reply = _links(_link(type="OWNS"), bad, _link(type="WORKS_FOR"))

    response = parse_pass2(reply)

    assert [r.type for r in response.relationships] == ["OWNS", "WORKS_FOR"]
    assert response.dropped == 1


def test_malformed_item_in_pass2_an_unknown_key_is_ignored_and_the_item_kept() -> None:
    """A leftover `event_candidate` flag or a made-up `confidence` costs nothing."""
    reply = _links(
        _link(type="OWNS", event_candidate=True, event_candidate_reason="x"),
        _link(type="WORKS_FOR", confidence=0.8),
    )

    response = parse_pass2(reply)

    assert [r.type for r in response.relationships] == ["OWNS", "WORKS_FOR"]
    assert response.dropped == 0


def test_malformed_item_in_pass2_a_whitespace_padded_key_is_accepted() -> None:
    """Surrounding whitespace on a key is stripped before the item is checked."""
    padded = {f" {k}\n" if k == "type" else f"\t{k}": v for k, v in _link().items()}

    response = parse_pass2(_links(padded))

    (link,) = response.relationships
    assert (link.type, link.from_id, link.to_id) == ("PARTICIPATED_IN", "e1", "e2")
    assert response.dropped == 0


def test_malformed_item_in_pass2_a_garbled_required_key_still_drops_the_item() -> None:
    """Only whitespace is repaired: a junk prefix on a required key leaves the
    field missing, so the item is dropped and counted once."""
    garbled = BAD_LINKS["garbled-required-key"]

    response = parse_pass2(_links(_link(type="OWNS"), garbled))

    assert [r.type for r in response.relationships] == ["OWNS"]
    assert response.dropped == 1


def test_malformed_item_every_relationship_bad_is_an_empty_result_not_bad_output() -> (
    None
):
    """The reply had the list, so it did not fail; it just held nothing usable."""
    response = parse_pass2(_links(*BAD_LINKS.values()))

    assert response.relationships == []
    assert response.dropped == len(BAD_LINKS)


def test_malformed_item_in_pass2_is_logged_by_error_type_and_field_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One error line per dropped relationship, with no sentence or key the
    Model wrote."""
    reply = _links(
        _link(),
        _link(type="MENTIONS", supporting_sentences=["Ada Verrin owns Karsk."]),
        {"\u0436from_id": "e1", **{k: v for k, v in _link().items() if k != "from_id"}},
    )

    with caplog.at_level(logging.ERROR):
        parse_pass2(reply)

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(lines) == 2
    assert all("relationship" in line for line in lines)
    assert "type" in lines[0] and "literal_error" in lines[0]
    assert "from_id" in lines[1] and "missing" in lines[1]
    text = " ".join(lines)
    for content in ("Verrin", "Karsk", "MENTIONS", "\u0436"):
        assert content not in text


def test_the_same_day_or_a_later_end_date_is_in_order() -> None:
    """`2024` to `2024-06` and equal dates are not reversed."""
    parse_pass2(_links(_link(date_from="2024-03", date_to="2024-03")))
    parse_pass2(_links(_link(date_from="2024", date_to="2024-06")))


# ---- the entity table, the Pass 2 prompt and the unresolvable links ----


def _grounded[Proposal: Pass1EntityProposal | Pass2RelationshipProposal](
    proposals: list[Proposal],
) -> list[Grounded[Proposal]]:
    """``proposals`` grounded in a Document text made of their own sentences."""
    text = "\n".join(s for p in proposals for s in p.supporting_sentences)
    return ground(proposals, DocumentText(text), "proposal").kept


def _table(*items: object) -> dict[str, Grounded[Pass1EntityProposal]]:
    return stamp_ids(_grounded(parse_pass1(_entities_reply(*items)).entities))


def _pass1() -> dict[str, Grounded[Pass1EntityProposal]]:
    return _table(
        _entity(attributes={"nationality": "Veldovan"}),
        _entity(
            "Arrival of the shipment in Karsk",
            label="Event",
            date="2024-02",
            place="Karsk",
        ),
        _entity("Karsk", label="Location"),
        _entity("Inspection of the yard", label="Event"),
    )


def _resolved(
    reply: str,
) -> tuple[list[Grounded[Pass2RelationshipProposal]], int]:
    """``reply`` parsed, grounded and resolved against ``_pass1()``'s entities."""
    links = _grounded(parse_pass2(reply).relationships)
    return known_relationships(links, _pass1())


def test_entities_get_short_ids_in_the_models_order() -> None:
    """e1, e2, ...: stamped by the worker, never by the Model."""
    table = _pass1()

    assert list(table) == ["e1", "e2", "e3", "e4"]
    assert [g.proposal.name for g in table.values()] == [
        "Ada Verrin",
        "Arrival of the shipment in Karsk",
        "Karsk",
        "Inspection of the yard",
    ]


def test_malformed_item_dropped_in_pass1_leaves_the_ids_unbroken() -> None:
    """The ids are stamped after the drop, so e1, e2 have no gap for Pass 2."""
    table = _table(_entity("Ada Verrin"), _entity(label="Dog"), _entity("MV Lark"))

    assert {i: g.proposal.name for i, g in table.items()} == {
        "e1": "Ada Verrin",
        "e2": "MV Lark",
    }


def test_pass2_messages_list_the_entities_by_id_then_the_body() -> None:
    """The user message names the pass, gives each entity's id, kind and name
    (and an Event's date and place when it has them), and holds the Document
    text."""
    system, user = pass2_messages("THE DOCUMENT TEXT", _pass1())

    assert system == {"role": "system", "content": load_prompt("system.md")}
    assert user["role"] == "user"
    content = user["content"]
    assert content.startswith("PASS: 2")
    assert '{"id":"e1","label":"Person","name":"Ada Verrin"}' in content
    assert (
        '{"id":"e2","label":"Event","name":"Arrival of the shipment in Karsk",'
        '"date":"2024-02","place":"Karsk"}'
    ) in content
    assert '{"id":"e4","label":"Event","name":"Inspection of the yard"}' in content
    assert "<<<BODY>>>\nTHE DOCUMENT TEXT\n<<<END BODY>>>" in content
    assert "{entities}" not in content and "{body}" not in content
    assert "signed" not in content and "Veldovan" not in content


def test_a_placeholder_inside_a_name_or_the_text_is_left_alone_in_pass2() -> None:
    """`{body}` in an entity name and `{entities}` in the article are text."""
    table = _table(_entity("Mr {body}"))

    _, user = pass2_messages("see {entities} here", table)

    assert '"name":"Mr {body}"' in user["content"]
    assert "see {entities} here" in user["content"]


def test_a_relationship_naming_an_unknown_id_is_dropped_and_counted() -> None:
    """Either end can be unknown; the known ones are kept in order."""
    kept, dropped = _resolved(
        _links(
            _link(from_id="e1", to_id="e2"),
            _link(from_id="e9", to_id="e2"),
            _link(from_id="e1", to_id="e7"),
            _link(from_id="Ada Verrin", to_id="e2"),
            _link(from_id="e2", to_id="e3", type="LOCATED_IN"),
        )
    )

    assert [(r.proposal.type, r.proposal.from_id, r.proposal.to_id) for r in kept] == [
        ("PARTICIPATED_IN", "e1", "e2"),
        ("LOCATED_IN", "e2", "e3"),
    ]
    assert dropped == 3


@pytest.mark.parametrize(
    ("from_id", "to_id"),
    [("e2", "e1"), ("e1", "e3"), ("e2", "e3"), ("e2", "e4"), ("e3", "e1")],
    ids=[
        "event-to-person",
        "person-to-location",
        "event-to-location",
        "event-to-event",
        "location-to-person",
    ],
)
def test_a_participant_link_must_go_from_a_non_event_to_an_event(
    from_id: str, to_id: str
) -> None:
    """`PARTICIPATED_IN` with an Event at the wrong end, or no Event at all, is
    dropped and counted like an unknown id."""
    kept, dropped = _resolved(_links(_link(from_id=from_id, to_id=to_id)))

    assert kept == [] and dropped == 1


@pytest.mark.parametrize(("from_id", "to_id"), [("e1", "e2"), ("e3", "e4")])
def test_a_participant_link_from_a_non_event_to_an_event_is_kept(
    from_id: str, to_id: str
) -> None:
    """Any non-Event can take part in any Event."""
    kept, dropped = _resolved(_links(_link(from_id=from_id, to_id=to_id)))

    assert len(kept) == 1 and dropped == 0


def test_other_types_may_have_an_event_at_either_end() -> None:
    """Only `PARTICIPATED_IN` has the Event rule."""
    kept, dropped = _resolved(
        _links(
            _link(type="LOCATED_IN", from_id="e2", to_id="e3"),
            _link(type="ASSOCIATED_WITH", from_id="e1", to_id="e4"),
            _link(type="ASSOCIATED_WITH", from_id="e2", to_id="e4"),
        )
    )

    assert len(kept) == 3 and dropped == 0


def test_no_unknown_ids_and_no_wrong_ends_drops_nothing() -> None:
    """The count is zero when every link resolves."""
    kept, dropped = _resolved(_links(_link()))

    assert len(kept) == 1 and dropped == 0
