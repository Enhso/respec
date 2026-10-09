"""Gate A evals: label checks, the recall rules, and the run cache.

The recall rules decide whether gate A passes, so each rule in
``evals/README.md`` has a test here, and so does each way a near miss must not
count. Every name is invented.
"""

from pathlib import Path
from typing import Any

import orjson
import pytest
from pydantic import ValidationError

from respec_worker.evals import (
    Attempt,
    Labels,
    Proposals,
    Tally,
    event_spans,
    lint,
    name_tokens,
    prompt_version,
    read_run,
    recalled_keys,
    run_model,
    score_article,
    score_run,
)
from respec_worker.extraction import DocumentText, load_prompt
from respec_worker.messages import (
    DocumentSummary,
    Entities,
    Failed,
    ProposedEntity,
    ProposedRelationship,
    Relationships,
)

BODY = (
    "Dolmen Freight LLC shipped the parts. Ada Verrin runs Dolmen Freight. "
    "On 12 February 2024 the cargo arrived in Port Karsk. "
    "Verrin met the buyer there."
)
SHIPPED, RUNS, ARRIVED, MET = (
    "Dolmen Freight LLC shipped the parts.",
    "Ada Verrin runs Dolmen Freight.",
    "On 12 February 2024 the cargo arrived in Port Karsk.",
    "Verrin met the buyer there.",
)


def _labels(**overrides: Any) -> Labels:
    """Labels for BODY: Ada Verrin, Dolmen Freight, Port Karsk, the arrival,
    and two links."""
    labels: dict[str, Any] = {
        "article": "a1",
        "checked": True,
        "entities": [
            {
                "id": "k1",
                "kind": "Person",
                "name": "Ada Verrin",
                "aliases": ["Verrin"],
                "sentences": [RUNS],
            },
            {
                "id": "k2",
                "kind": "Organization",
                "name": "Dolmen Freight LLC",
                "aliases": ["Dolmen Freight"],
                "sentences": [SHIPPED],
            },
            {
                "id": "k3",
                "kind": "Location",
                "name": "Port Karsk",
                "sentences": [ARRIVED],
            },
            {
                "id": "k4",
                "kind": "Event",
                "name": "Arrival of the cargo in Port Karsk",
                "aliases": ["cargo arrival"],
                "date": "2024-02-12",
                "sentences": [ARRIVED],
            },
        ],
        "relationships": [
            {
                "from": "k1",
                "to": "k2",
                "types": ["CONTROLS", "WORKS_FOR"],
                "sentence": RUNS,
            },
            {
                "from": "k2",
                "to": "k4",
                "types": ["PARTICIPATED_IN"],
                "sentence": ARRIVED,
            },
        ],
    }
    labels.update(overrides)
    return Labels.model_validate(labels)


def _entity(
    entity_id: str, label: str, name: str, sentence: str = SHIPPED
) -> ProposedEntity:
    start = BODY.index(sentence)
    return ProposedEntity(
        id=entity_id,
        label=label,
        name=name,
        sentence=sentence,
        sentence_start=start,
        sentence_end=start + len(sentence),
    )


def _link(kind: str, from_id: str, to_id: str) -> ProposedRelationship:
    start = BODY.index(RUNS)
    return ProposedRelationship(
        type=kind,
        from_id=from_id,
        to_id=to_id,
        date_from=None,
        date_to=None,
        date_precision="unknown",
        sentence=RUNS,
        sentence_start=start,
        sentence_end=start + len(RUNS),
    )


def _recalls(entity: ProposedEntity) -> set[str]:
    labels = _labels()
    return recalled_keys(entity, labels, event_spans(labels, DocumentText(BODY)))


# ---- names ----


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Ada Verrin", "ada  VERRIN"),
        ("Arlenko-Vėtrinas", "Arlenko Vetrinas"),
        ("The Dolmen Group", "Dolmen Group"),
        ("“Dolmen” Freight, LLC.", "dolmen freight llc"),
    ],
)
def test_evals_name_forms_ignore_case_accents_punctuation_and_a_leading_the(
    a: str, b: str
) -> None:
    assert name_tokens(a) == name_tokens(b)


def test_evals_a_bare_the_is_kept_as_a_name() -> None:
    assert name_tokens("The") == ("the",)


# ---- entity and event recall ----


def test_evals_an_exact_alias_recalls_whatever_kind_was_proposed() -> None:
    """Equal names are strong enough: an Organization "Port Karsk" still
    recalls the Location."""
    assert _recalls(_entity("e1", "Organization", "port karsk")) == {"k3"}


def test_evals_a_name_containing_a_form_recalls_when_the_kinds_fit() -> None:
    assert _recalls(_entity("e1", "Organization", "Dolmen Freight LLC (Karsk)")) == {
        "k2"
    }
    assert _recalls(_entity("e1", "Identity", "Ada Verrin, the director")) == {"k1"}


def test_evals_a_name_containing_a_form_of_another_kind_recalls_nothing() -> None:
    """A Person named after her company is not the company."""
    assert (
        _recalls(_entity("e1", "Person", "Dolmen Freight director Mira Osk")) == set()
    )


def test_evals_containment_is_by_whole_words() -> None:
    assert _recalls(_entity("e1", "Person", "Adam Verrinson")) == set()


def test_evals_an_event_recalls_by_overlapping_sentence_whatever_its_name() -> None:
    proposed = _entity("e1", "Event", "Shipment reaches the port", ARRIVED)

    assert _recalls(proposed) == {"k4"}


def test_evals_only_a_proposed_event_recalls_by_sentence() -> None:
    proposed = _entity("e1", "Organization", "Karsk port authority", ARRIVED)

    assert _recalls(proposed) == set()


def test_evals_an_event_elsewhere_with_another_name_recalls_nothing() -> None:
    proposed = _entity("e1", "Event", "Meeting with the buyer", MET)

    assert _recalls(proposed) == set()


def test_evals_an_event_named_as_one_key_event_is_not_credited_by_sentence_too() -> (
    None
):
    """The arrival sentence reports a second Event; a proposal named as the
    arrival recalls only the arrival."""
    labels = _labels(
        entities=[
            *_labels().model_dump(by_alias=True)["entities"],
            {
                "id": "k5",
                "kind": "Event",
                "name": "Customs inspection",
                "sentences": [ARRIVED],
            },
        ]
    )
    proposed = _entity("e1", "Event", "Cargo arrival", ARRIVED)

    assert recalled_keys(proposed, labels, event_spans(labels, DocumentText(BODY))) == {
        "k4"
    }


def test_evals_an_event_recalls_by_alias_anywhere() -> None:
    proposed = _entity("e1", "Event", "The cargo arrival", MET)

    assert _recalls(proposed) == {"k4"}


# ---- run scores ----


def _proposals() -> Proposals:
    return Proposals(
        entities=[
            _entity("e1", "Person", "Ada Verrin", RUNS),
            _entity("e2", "Organization", "Dolmen Freight"),
            _entity("e3", "Event", "Cargo reaches Port Karsk", ARRIVED),
            _entity("e4", "Person", "Unlabelled Buyer", MET),
        ],
        relationships=[
            _link("WORKS_FOR", "e2", "e1"),
            _link("ASSOCIATED_WITH", "e2", "e3"),
            _link("OWNS", "e4", "e1"),
        ],
    )


def test_evals_a_run_scores_entities_events_and_relationships_apart() -> None:
    """Port Karsk was missed; the Verrin link counts reversed and typed; the
    arrival link counts only with any type."""
    score = score_run(_labels(), DocumentText(BODY), _proposals())

    assert score.entities == Tally(2, 3)
    assert score.events == Tally(1, 1)
    assert score.relationships == Tally(1, 2)
    assert score.relationships_any_type == Tally(2, 2)


def test_evals_a_link_to_an_unrecalled_end_does_not_count() -> None:
    run = Proposals(
        entities=[_entity("e1", "Person", "Ada Verrin", RUNS)],
        relationships=[_link("WORKS_FOR", "e1", "e9")],
    )

    score = score_run(_labels(), DocumentText(BODY), run)

    assert score.relationships == Tally(0, 2)
    assert score.relationships_any_type == Tally(0, 2)


def test_evals_an_empty_run_recalls_nothing() -> None:
    score = score_run(_labels(), DocumentText(BODY), Proposals([], []))

    assert (score.entities, score.events, score.relationships) == (
        Tally(0, 3),
        Tally(0, 1),
        Tally(0, 2),
    )


def test_evals_recall_is_none_without_labelled_items() -> None:
    assert Tally(0, 0).recall is None
    assert Tally(3, 4).recall == 0.75


# ---- labels ----


@pytest.mark.parametrize(
    "change",
    [
        {
            "relationships": [
                {"from": "k1", "to": "k9", "types": ["OWNS"], "sentence": RUNS}
            ]
        },
        {
            "relationships": [
                {"from": "k1", "to": "k1", "types": ["OWNS"], "sentence": RUNS}
            ]
        },
        {
            "relationships": [
                {
                    "from": "k1",
                    "to": "k2",
                    "types": ["PARTICIPATED_IN"],
                    "sentence": RUNS,
                }
            ]
        },
        {"relationships": [{"from": "k1", "to": "k2", "types": [], "sentence": RUNS}]},
        {
            "relationships": [
                {"from": "k1", "to": "k2", "types": ["KNOWS"], "sentence": RUNS}
            ]
        },
        {
            "entities": [
                {"id": "k1", "kind": "Person", "name": "A", "sentences": [RUNS]},
                {"id": "k1", "kind": "Person", "name": "B", "sentences": [RUNS]},
            ],
            "relationships": [],
        },
        {
            "entities": [
                {
                    "id": "k1",
                    "kind": "Person",
                    "name": "A",
                    "date": "2024",
                    "sentences": [RUNS],
                }
            ],
            "relationships": [],
        },
        {"surprise": 1},
    ],
    ids=[
        "unknown-id",
        "self-link",
        "participant-to-non-event",
        "no-types",
        "unknown-type",
        "duplicate-id",
        "date-on-person",
        "unknown-key",
    ],
)
def test_evals_malformed_labels_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _labels(**change)


def test_evals_lint_passes_good_labels() -> None:
    assert lint(_labels(), BODY) == []


def test_evals_lint_reports_unfound_sentences_and_absent_names() -> None:
    labels = _labels(
        entities=[
            {
                "id": "k1",
                "kind": "Person",
                "name": "Mira Osk",
                "sentences": ["Osk fled."],
            },
            {"id": "k2", "kind": "Event", "name": "Flight of Osk", "sentences": [MET]},
        ],
        relationships=[
            {
                "from": "k1",
                "to": "k2",
                "types": ["PARTICIPATED_IN"],
                "sentence": "Nope.",
            }
        ],
    )

    problems = lint(labels, BODY)

    assert len(problems) == 3
    assert sum("sentence not found" in p for p in problems) == 2
    assert sum("no form of its name" in p for p in problems) == 1


def test_evals_lint_finds_a_name_whatever_its_accents_and_case() -> None:
    labels = _labels(
        entities=[
            {
                "id": "k1",
                "kind": "Organization",
                "name": "DOLMÉN FREIGHT",
                "sentences": [SHIPPED],
            }
        ],
        relationships=[],
    )

    assert lint(labels, BODY) == []


# ---- the run cache ----


def test_evals_runs_are_kept_by_prompt_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Changing any prompt file changes the folder its runs go in."""
    before = prompt_version()
    original = load_prompt
    monkeypatch.setattr(
        "respec_worker.evals.load_prompt",
        lambda name: original(name) + ("!" if name == "pass2_relationships.md" else ""),
    )

    assert len(before) == 8
    assert prompt_version() != before


def _stdout(*messages: Entities | Relationships | Failed) -> bytes:
    return b"".join(orjson.dumps(m.model_dump(mode="json")) + b"\n" for m in messages)


def _success() -> Attempt:
    entities = Entities(
        provider="gemini",
        model="m",
        document=DocumentSummary(url=None, title=None, chars=len(BODY)),
        entities=_proposals().entities,
        dropped=0,
        sentences_dropped=0,
    )
    relationships = Relationships(
        provider="gemini",
        model="m",
        relationships=_proposals().relationships,
        dropped=0,
        sentences_dropped=0,
    )
    return Attempt(_stdout(entities, relationships), True, None, 1.0)


def _failure(reason: str) -> Attempt:
    failed = Failed(provider="gemini", reason=reason, message=f"{reason} message")  # type: ignore[arg-type]
    return Attempt(_stdout(failed), False, reason, 1.0, failed.message)


def _run(
    tmp_path: Path, attempts: list[Attempt], articles: list[str], **kwargs: Any
) -> tuple[int, int]:
    """Run over ``articles`` with ``attempts`` in turn; return the exit code
    and how many attempts were made."""
    queue = list(attempts)
    made = 0

    def extract(body: Path) -> Attempt:
        nonlocal made
        made += 1
        return queue.pop(0)

    code = run_model(
        "gemini",
        "vendor/model:free",
        articles,
        bodies=tmp_path,
        runs=tmp_path / "runs",
        extract=extract,
        say=lambda line: None,
        **kwargs,
    )
    return code, made


def test_evals_run_saves_each_success_and_logs_every_attempt(tmp_path: Path) -> None:
    code, made = _run(
        tmp_path, [_success(), _failure("bad_output"), _success()], ["a1"], samples=2
    )

    folder = tmp_path / "runs" / "vendor_model_free" / "a1"
    assert (code, made) == (0, 3)
    assert sorted(p.name for p in folder.glob("s*.jsonl")) == ["s1.jsonl", "s2.jsonl"]
    log = [
        orjson.loads(line) for line in (folder / "log.jsonl").read_bytes().splitlines()
    ]
    assert [(line["ok"], line["reason"], line["message"]) for line in log] == [
        (True, None, None),
        (False, "bad_output", "bad_output message"),
        (True, None, None),
    ]
    run = read_run(folder / "s1.jsonl")
    assert [e.name for e in run.entities] == [e.name for e in _proposals().entities]
    assert len(run.relationships) == 3


def test_evals_run_resumes_without_redoing_saved_samples(tmp_path: Path) -> None:
    _run(tmp_path, [_success()], ["a1"], samples=1)

    code, made = _run(tmp_path, [_success(), _success()], ["a1"], samples=2)

    assert (code, made) == (0, 1)


def test_evals_run_gives_up_on_an_article_after_its_failures(tmp_path: Path) -> None:
    attempts = [_failure("bad_output")] * 2 + [_success()]

    code, made = _run(tmp_path, attempts, ["a1", "a2"], samples=1, max_failures=2)

    assert (code, made) == (0, 3)
    assert not list((tmp_path / "runs" / "vendor_model_free" / "a1").glob("s*.jsonl"))
    assert (tmp_path / "runs" / "vendor_model_free" / "a2" / "s1.jsonl").exists()


def test_evals_run_tries_again_an_article_an_earlier_run_gave_up(
    tmp_path: Path,
) -> None:
    """A Provider outage that retired an article in one run does not retire
    it for the next."""
    _run(tmp_path, [_failure("other")], ["a1"], samples=1, max_failures=1)

    code, made = _run(tmp_path, [_success()], ["a1"], samples=1, max_failures=1)

    assert (code, made) == (0, 1)


def test_evals_run_stops_on_two_rate_limits_in_a_row(tmp_path: Path) -> None:
    """A spent quota stops the run, and its failures do not count against the
    article, so a later run still tries it."""
    attempts = [_failure("rate_limit"), _failure("quota")]

    code, made = _run(tmp_path, attempts, ["a1", "a2"], samples=1, max_failures=1)

    assert (code, made) == (1, 2)
    code, made = _run(
        tmp_path, [_success(), _success()], ["a1", "a2"], samples=1, max_failures=1
    )
    assert (code, made) == (0, 2)


def test_evals_run_does_not_count_a_rate_limit_against_the_article(
    tmp_path: Path,
) -> None:
    code, made = _run(
        tmp_path,
        [_failure("rate_limit"), _success()],
        ["a1"],
        samples=1,
        max_failures=1,
    )

    assert (code, made) == (0, 2)
    assert (tmp_path / "runs" / "vendor_model_free" / "a1" / "s1.jsonl").exists()


def test_evals_score_article_sums_its_samples(tmp_path: Path) -> None:
    folder = tmp_path / "a1"
    folder.mkdir()
    for n in (1, 2):
        (folder / f"s{n}.jsonl").write_bytes(_success().stdout)
    (folder / "log.jsonl").write_bytes(
        b'{"ok":true,"reason":null,"seconds":4.0,"at":"x"}\n'
        b'{"ok":false,"reason":"bad_output","seconds":2.0,"at":"x"}\n'
    )

    result = score_article(_labels(), BODY, folder)

    assert result.samples == 2
    assert result.score.entities == Tally(4, 6)
    assert result.failures == {"bad_output": 1}
    assert result.entity_counts == [4, 4]
