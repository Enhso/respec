"""The contract fixtures: every worker message in `contracts/fixtures/messages/`
validates against the worker's own models (ISC-17, worker side), and each kind,
failure reason and progress stage has a fixture."""

from pathlib import Path
from typing import get_args

from respec_worker.messages import (
    MESSAGE_ADAPTER,
    Entities,
    Failed,
    Progress,
    Reason,
    Relationships,
    Stage,
)

MESSAGES_DIR = (
    Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "messages"
)


def test_contract_fixtures_validate() -> None:
    """Every fixture file validates, the folder is not empty, and each message
    kind, failure reason and progress stage has a fixture."""
    files = sorted(MESSAGES_DIR.glob("*.json"))
    assert files, f"no fixtures in {MESSAGES_DIR}"

    messages = [MESSAGE_ADAPTER.validate_json(path.read_bytes()) for path in files]

    reasons = {m.reason for m in messages if isinstance(m, Failed)}
    assert reasons == set(get_args(Reason))
    stages = {m.stage for m in messages if isinstance(m, Progress)}
    assert stages == set(get_args(Stage))
    kinds = {"started", "progress", "done", "entities", "relationships", "failed"}
    assert {m.kind for m in messages} == kinds


def test_contract_fixtures_exercise_the_two_pass_shapes() -> None:
    """The new fields are not only nullable: some fixture fills each of them."""
    messages = [
        MESSAGE_ADAPTER.validate_json(path.read_bytes())
        for path in sorted(MESSAGES_DIR.glob("*.json"))
    ]

    events = [
        e
        for m in messages
        if isinstance(m, Entities)
        for e in m.entities
        if e.label == "Event"
    ]
    assert any(e.date and e.place for e in events)
    assert any(e.date is None and e.place is None for e in events)
    entities = [m for m in messages if isinstance(m, Entities)]
    assert any(m.dropped > 0 for m in entities)
    assert any(m.dropped == 0 for m in entities)
    assert any(m.sentences_dropped > 0 for m in entities)
    assert any(isinstance(m, Entities) and m.document.url is None for m in messages)
    assert any(
        isinstance(m, Progress) and (m.pass_number, m.pass_count) == (1, 2)
        for m in messages
    )
    assert any(isinstance(m, Progress) and m.pass_number is None for m in messages)
    relationships = [m for m in messages if isinstance(m, Relationships)]
    assert any(m.dropped > 0 for m in relationships)
    assert any(m.sentences_dropped > 0 for m in relationships)
    assert any(
        r.date_from and r.date_precision for m in relationships for r in m.relationships
    )


def test_the_participant_fixture_links_an_entity_to_an_event() -> None:
    """`relationships.json` names the ids of `entities.json`, and each
    `PARTICIPATED_IN` in it ends at an Event and starts at something else."""
    entities = MESSAGE_ADAPTER.validate_json(
        (MESSAGES_DIR / "entities.json").read_bytes()
    )
    relationships = MESSAGE_ADAPTER.validate_json(
        (MESSAGES_DIR / "relationships.json").read_bytes()
    )
    assert isinstance(entities, Entities) and isinstance(relationships, Relationships)
    labels = {e.id: e.label for e in entities.entities}

    participants = [
        r for r in relationships.relationships if r.type == "PARTICIPATED_IN"
    ]

    assert participants
    assert all(labels[r.to_id] == "Event" for r in participants)
    assert all(labels[r.from_id] != "Event" for r in participants)
    assert all(
        r.from_id in labels and r.to_id in labels for r in relationships.relationships
    )


def test_contract_fixture_offsets_are_code_points_of_the_sentence() -> None:
    """Each sentence's offsets span exactly its characters (end exclusive), and
    an entity's sentence lies inside its Document's `chars`."""
    messages = [
        MESSAGE_ADAPTER.validate_json(path.read_bytes())
        for path in sorted(MESSAGES_DIR.glob("*.json"))
    ]

    checked = 0
    for message in messages:
        if isinstance(message, Entities):
            for entity in message.entities:
                assert entity.sentence_end - entity.sentence_start == len(
                    entity.sentence
                )
                assert 0 <= entity.sentence_start
                assert entity.sentence_end <= message.document.chars
                checked += 1
        elif isinstance(message, Relationships):
            for link in message.relationships:
                assert link.sentence_end - link.sentence_start == len(link.sentence)
                assert 0 <= link.sentence_start
                checked += 1
    assert checked >= 5
    assert any(
        not entity.sentence.isascii()
        for message in messages
        if isinstance(message, Entities)
        for entity in message.entities
    )
