"""Pass 1: the prompt files, and parsing the Model's reply."""

import pytest

from respec_worker.extraction import (
    PASS1_MAX_TOKENS,
    BadOutput,
    load_prompt,
    parse_pass1,
    pass1_messages,
)

ONE_ENTITY = (
    '{"entities": [{"label": "Person", "name": "Ada Verrin", '
    '"supporting_sentences": ["Ada Verrin signed the order."]}]}'
)


def test_the_prompt_files_load_from_the_package() -> None:
    """Both ported prompts are package data, not read from the working directory."""
    assert "Pass 1 — Entities" in load_prompt("system.md")
    assert "{body}" in load_prompt("pass1_entities.md")


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
        _entity_json("Dog", "Rex", '["a"]'),
        _entity_json("Person", "Ada Verrin", "[]"),
        _entity_json("Person", "", '["a"]'),
        '{"entities": "none"}',
        '{"people": []}',
        "[]",
    ],
    ids=[
        "empty",
        "blank",
        "prose-only",
        "cut-off",
        "invalid-json",
        "unknown-label",
        "no-sentence",
        "empty-name",
        "entities-not-a-list",
        "wrong-key",
        "top-level-list",
    ],
)
def test_a_reply_that_is_not_valid_pass1_json_is_bad_output(reply: str) -> None:
    """Each way the reply can be wrong raises `BadOutput` with a plain sentence
    that holds none of the reply."""
    with pytest.raises(BadOutput) as excinfo:
        parse_pass1(reply)

    message = excinfo.value.message
    assert message == str(excinfo.value)
    assert message.endswith(".")
    assert "Ada Verrin" not in message and "Rex" not in message
