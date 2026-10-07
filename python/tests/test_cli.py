"""Tests for the worker command-line interface."""

import os
import subprocess
import sys
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import get_args

import httpx
import orjson
import pytest

from respec_worker.cli import main
from respec_worker.extraction import PASS1_MAX_TOKENS
from respec_worker.messages import (
    MESSAGE_ADAPTER,
    AnyMessage,
    DocumentSummary,
    Done,
    Entities,
    Failed,
    Progress,
    ProposedEntity,
    ProposedRelationship,
    Relationships,
    Started,
)
from respec_worker.providers import PROVIDERS, Provider

KEY = "secret-key-0123456789"
ARTICLES = Path(__file__).parent / "fixtures" / "articles"
ARTICLE_URL = "https://news.example/articles/42"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """`--version` prints the package version and exits with status 0."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "respec-worker 0.1.0\n"


def test_console_script_points_at_main() -> None:
    """The `respec-worker` console script resolves to `cli.main`."""
    (ep,) = [
        ep for ep in entry_points(group="console_scripts") if ep.name == "respec-worker"
    ]
    assert ep.load() is main


def _lines(text: str) -> list[AnyMessage]:
    return [MESSAGE_ADAPTER.validate_json(line) for line in text.splitlines()]


@pytest.mark.parametrize("provider", get_args(Provider))
def test_test_call_success_emits_started_then_done(
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A mocked success prints `started` then `done`, exits 0, and leaks no key."""
    spec = PROVIDERS[provider]
    monkeypatch.setenv(spec.key_env, KEY)
    answer = {"choices": [{"message": {"content": "pong"}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=answer))

    code = main(["test-call", "--provider", provider], transport=transport)

    captured = capsys.readouterr()
    assert code == 0
    assert _lines(captured.out) == [
        Started(provider=provider, model=spec.default_model),
        Done(provider=provider, model=spec.default_model, reply="pong"),
    ]
    assert KEY not in captured.out + captured.err


def test_test_call_failure_emits_started_then_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mocked 401 prints `started` then `failed` with reason `auth`, exits 1,
    and neither the key nor the raw response body reaches stdout or stderr."""
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, text=f"bad key {KEY} RAW-BODY")
    )

    code = main(["test-call", "--provider", "openrouter"], transport=transport)

    captured = capsys.readouterr()
    assert code == 1
    started, failed = _lines(captured.out)
    assert isinstance(started, Started)
    assert isinstance(failed, Failed)
    assert failed.reason == "auth"
    assert KEY not in captured.out + captured.err
    assert "RAW-BODY" not in captured.out + captured.err


def test_test_call_without_a_key_fails_without_calling_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing key prints `failed` with reason `auth` and makes no request."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a call was made without a key")

    code = main(
        ["test-call", "--provider", "gemini"],
        transport=httpx.MockTransport(no_network),
    )

    (failed,) = _lines(capsys.readouterr().out)
    assert code == 1
    assert isinstance(failed, Failed)
    assert failed.reason == "auth"
    assert "No Google Gemini key is set" in failed.message


def test_test_call_rejects_an_unknown_provider() -> None:
    """argparse refuses a Provider outside the table."""
    with pytest.raises(SystemExit) as excinfo:
        main(["test-call", "--provider", "nowhere"])
    assert excinfo.value.code == 2


def test_no_arguments_prints_help_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no arguments the help lists the `test-call` command."""
    assert main([]) == 0
    assert "test-call" in capsys.readouterr().out


# ---- test-extraction ----

REPLY = (
    "```json\n"
    '{"entities": ['
    '{"label": "Organization", "name": "State Review Directorate", '
    '"supporting_sentences": ["A source inside the State Review Directorate left.", '
    '"A second sentence."]}, '
    '{"label": "Person", "name": "Ada Verrin", '
    '"supporting_sentences": ["Ada Verrin signed the order."]}'
    "]}\n```"
)


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class Network:
    """A fake network: the article site serves one page, and the Provider
    answers the queued responses in turn (the last one repeats)."""

    def __init__(
        self, *chat_responses: httpx.Response, page: str = "campaign_documents"
    ) -> None:
        self.chat_responses = list(chat_responses)
        self.page = (ARTICLES / f"{page}.html").read_text(encoding="utf-8")
        self.article_status = 200
        self.requests: list[httpx.Request] = []

    @property
    def chat_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host != "news.example"]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "news.example":
            return httpx.Response(self.article_status, text=self.page)
        queue = self.chat_responses
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _extract(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    provider: Provider = "gemini",
    sleeps: list[float] | None = None,
) -> tuple[int, list[AnyMessage]]:
    """Run `test-extraction` over the fake network; return the exit code and
    stdout's messages. Fails if the key shows up in stdout or stderr."""
    monkeypatch.setenv(PROVIDERS[provider].key_env, KEY)
    sleep: Callable[[float], None] = (sleeps if sleeps is not None else []).append
    code = main(
        ["test-extraction", "--provider", provider, "--url", ARTICLE_URL],
        transport=network.transport,
        sleep=sleep,
    )
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    return code, _lines(captured.out)


@pytest.mark.parametrize("provider", get_args(Provider))
def test_test_extraction_fetches_calls_once_and_reports_the_proposals(
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Progress for each stage, then `entities` whose sentence is the first
    supporting sentence; exit 0; one Provider call with the whole budget."""
    spec = PROVIDERS[provider]
    network = Network(_chat_reply(REPLY), page="campaign_documents")

    code, messages = _extract(network, capsys, monkeypatch, provider)

    assert code == 0
    *working, final = messages
    assert isinstance(final, Entities)
    chars = final.document.chars
    assert chars > 1000
    assert working == [
        Progress(provider=provider, stage="fetching", detail="Fetching the article"),
        Progress(
            provider=provider,
            stage="calling_model",
            detail=f"Calling {spec.default_model} on {chars:,} characters",
        ),
    ]
    assert final == Entities(
        provider=provider,
        model=spec.default_model,
        document=DocumentSummary(
            url=ARTICLE_URL,
            title="Inside Halvard's quiet campaign across the Lower Marches",
            chars=chars,
        ),
        entities=[
            ProposedEntity(
                id="e1",
                label="Organization",
                name="State Review Directorate",
                sentence="A source inside the State Review Directorate left.",
            ),
            ProposedEntity(
                id="e2",
                label="Person",
                name="Ada Verrin",
                sentence="Ada Verrin signed the order.",
            ),
        ],
        dropped=0,
    )
    (request,) = network.chat_requests
    assert request.url.host == spec.host
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = orjson.loads(request.content)
    assert body["model"] == spec.default_model
    assert body["max_tokens"] == PASS1_MAX_TOKENS == 16384
    system, user = body["messages"]
    assert system["role"] == "system" and "Pass 1" in system["content"]
    assert user["role"] == "user" and "Halvard Centre" in user["content"]


def test_the_key_goes_only_to_the_providers_host_never_to_the_article_site(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The article request carries no Authorization header."""
    network = Network(_chat_reply(REPLY), page="campaign_documents")

    _extract(network, capsys, monkeypatch)

    (article_request,) = [r for r in network.requests if r.url.host == "news.example"]
    assert "authorization" not in article_request.headers
    assert KEY not in str(article_request.url)


def test_a_page_without_a_title_gives_a_null_title(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No title on the page is `title: null`, not an empty string."""
    network = Network(_chat_reply(REPLY))
    paragraph = "<p>" + "A sentence about a shipment of machine tools. " * 8 + "</p>"
    network.page = f"<html><body><article>{paragraph * 4}</article></body></html>"

    code, messages = _extract(network, capsys, monkeypatch)

    final = messages[-1]
    assert code == 0
    assert isinstance(final, Entities)
    assert final.document.title is None


def test_a_429_twice_then_a_reply_waits_and_carries_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two `waiting_rate_limit` messages, each before its wait, then success."""
    network = Network(
        httpx.Response(429),
        httpx.Response(429, headers={"Retry-After": "20"}),
        _chat_reply(REPLY),
        page="campaign_documents",
    )
    sleeps: list[float] = []

    code, messages = _extract(network, capsys, monkeypatch, sleeps=sleeps)

    assert code == 0
    waiting = [m for m in messages if isinstance(m, Progress) and m.stage != "fetching"]
    assert [m.stage for m in waiting] == [
        "calling_model",
        "waiting_rate_limit",
        "waiting_rate_limit",
    ]
    assert [m.detail for m in waiting[1:]] == [
        "Rate limited; retrying in 5 s (attempt 2 of 6)",
        "Rate limited; retrying in 20 s (attempt 3 of 6)",
    ]
    assert sleeps == [5.0, 20.0]
    assert isinstance(messages[-1], Entities)
    assert len(network.chat_requests) == 3


def test_a_429_on_every_attempt_fails_with_rate_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Five waits, six requests, then `failed` with reason `rate_limit`."""
    network = Network(httpx.Response(429), page="campaign_documents")
    sleeps: list[float] = []

    code, messages = _extract(network, capsys, monkeypatch, sleeps=sleeps)

    assert code == 1
    stages = [m.stage for m in messages if isinstance(m, Progress)]
    assert stages == ["fetching", "calling_model"] + ["waiting_rate_limit"] * 5
    final = messages[-1]
    assert isinstance(final, Failed)
    assert final.reason == "rate_limit"
    assert len(network.chat_requests) == 6
    assert sleeps == [5.0, 10.0, 20.0, 40.0, 60.0]


@pytest.mark.parametrize(
    "reply",
    ["I found no entities.", '{"entities": "none"}', '{"entities'],
)
def test_a_reply_that_is_not_pass1_json_fails_with_bad_output(
    reply: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The run reports `bad_output` after the call, with no entities message."""
    network = Network(_chat_reply(reply), page="campaign_documents")

    code, messages = _extract(network, capsys, monkeypatch)

    assert code == 1
    assert [m.kind for m in messages] == ["progress", "progress", "failed"]
    final = messages[-1]
    assert isinstance(final, Failed)
    assert final.reason == "bad_output"


@pytest.mark.parametrize(
    ("page", "status"), [("campaign_documents", 404), ("empty_body", 200)]
)
def test_an_article_that_cannot_be_fetched_fails_before_any_model_call(
    page: str,
    status: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bad status or a page with no body is `fetch`, and the Provider is not
    called."""
    network = Network(_chat_reply(REPLY), page=page)
    network.article_status = status

    code, messages = _extract(network, capsys, monkeypatch)

    assert code == 1
    assert [m.kind for m in messages] == ["progress", "failed"]
    final = messages[-1]
    assert isinstance(final, Failed)
    assert final.reason == "fetch"
    assert network.chat_requests == []


def test_a_provider_failure_is_relayed_without_the_key_or_the_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 401 is `failed` with `auth`; neither the key nor the raw body is shown."""
    network = Network(
        httpx.Response(401, text=f"bad key {KEY} RAW-BODY"), page="campaign_documents"
    )

    code, messages = _extract(network, capsys, monkeypatch)

    assert code == 1
    final = messages[-1]
    assert isinstance(final, Failed)
    assert final.reason == "auth"
    assert "RAW-BODY" not in final.message


def test_test_extraction_without_a_key_fails_without_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No key set is `auth`, and neither the article nor the Provider is asked."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    network = Network(_chat_reply(REPLY))

    code = main(
        ["test-extraction", "--provider", "gemini", "--url", ARTICLE_URL],
        transport=network.transport,
    )

    (failed,) = _lines(capsys.readouterr().out)
    assert code == 1
    assert isinstance(failed, Failed)
    assert failed.reason == "auth"
    assert network.requests == []


def test_test_extraction_needs_a_provider_and_a_url() -> None:
    """argparse refuses a missing `--url` and an unknown Provider."""
    for argv in (
        ["test-extraction", "--provider", "gemini"],
        ["test-extraction", "--url", ARTICLE_URL],
        ["test-extraction", "--provider", "nowhere", "--url", ARTICLE_URL],
    ):
        with pytest.raises(SystemExit) as excinfo:
            main(argv)
        assert excinfo.value.code == 2


def test_messages_are_written_as_utf8_whatever_the_stdout_encoding() -> None:
    """An entity name such as 黄辉 must not crash a worker whose locale is ASCII."""
    code = (
        "from respec_worker.messages import Failed, emit; "
        "emit(Failed(provider='gemini', reason='other', message='Huang Hui (黄辉)'))"
    )
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"}

    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    (line,) = result.stdout.splitlines()
    message = MESSAGE_ADAPTER.validate_json(line)
    assert isinstance(message, Failed)
    assert message.message == "Huang Hui (黄辉)"


# ---- extract ----

SHIPMENT_URL = "https://news.example/articles/42"

SHIPMENT_ENTITIES = [
    {
        "label": "Location",
        "name": "Port Verrin",
        "supporting_sentences": [
            "This investigation traces a single shipment of dual-use machine tools "
            "from Port Verrin to a workshop on the outskirts of Karsk."
        ],
    },
    {
        "label": "Location",
        "name": "Karsk",
        "supporting_sentences": ["The shipment arrived in Karsk in February 2024."],
    },
    {
        "label": "Organization",
        "name": "Tidemark",
        "supporting_sentences": [
            "A reporter for Tidemark reached out to the registered director."
        ],
    },
    {
        "label": "Organization",
        "name": "Veldovan defence procurement network",
        "supporting_sentences": [
            "The consignee is one of three known front companies linked to a "
            "sanctioned Veldovan defence procurement network."
        ],
    },
    {
        "label": "Event",
        "name": "Arrival of the shipment in Karsk",
        "supporting_sentences": ["The shipment arrived in Karsk in February 2024."],
        "date": "2024-02",
        "place": "Karsk",
    },
    {
        "label": "Event",
        "name": "Lantern-4 imagery check of the Karsk yard",
        "supporting_sentences": [
            "We were able to confirm the receipt by matching containers visible in "
            "Lantern-4 imagery from 12 February 2024."
        ],
        "date": "2024-02-12",
        "place": "Karsk",
    },
]

SHIPMENT_LINKS = [
    {
        "type": "PARTICIPATED_IN",
        "from_id": "e4",
        "to_id": "e5",
        "date_from": "2024-02",
        "date_precision": "month",
        "supporting_sentences": ["The shipment arrived in Karsk in February 2024."],
    },
    {
        "type": "PARTICIPATED_IN",
        "from_id": "e3",
        "to_id": "e6",
        "date_from": "2024-02-12",
        "date_precision": "day",
        "supporting_sentences": [
            "We were able to confirm the receipt by matching containers visible in "
            "Lantern-4 imagery from 12 February 2024."
        ],
    },
    {
        "type": "LOCATED_IN",
        "from_id": "e1",
        "to_id": "e2",
        "date_precision": "unknown",
        "supporting_sentences": ["A second sentence."],
    },
]


def _json_reply(**document: object) -> httpx.Response:
    return _chat_reply(orjson.dumps(document).decode())


def _shipment_network(*extra: httpx.Response) -> Network:
    """The shipment article, then a Pass 1 reply, a Pass 2 reply and `extra`."""
    return Network(
        _json_reply(entities=SHIPMENT_ENTITIES),
        _json_reply(relationships=SHIPMENT_LINKS),
        *extra,
        page="shipment_trace",
    )


def _extract_command(
    network: Network,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *source: str,
    sleeps: list[float] | None = None,
) -> tuple[int, list[AnyMessage]]:
    """Run `extract` with the given `--url ...` or `--text-file ...` arguments
    over the fake network; return the exit code and stdout's messages. Fails if
    the key shows up in stdout or stderr."""
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    sleep: Callable[[float], None] = (sleeps if sleeps is not None else []).append
    code = main(
        ["extract", "--provider", "gemini", *source],
        transport=network.transport,
        sleep=sleep,
    )
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err
    return code, _lines(captured.out)


def test_two_pass_events_extract_makes_two_calls_and_links_participants_to_events(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ISC-61 on a fixture article with a fake transport: exactly two Model
    calls; an Event Entity carries a date and a place; a participant
    Relationship links an Entity to it."""
    spec = PROVIDERS["gemini"]
    network = _shipment_network()

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 0
    assert len(network.chat_requests) == 2
    kinds = [m.kind for m in messages]
    assert kinds == ["progress", "progress", "entities", "progress", "relationships"]
    fetching, pass1, entities, pass2, relationships = messages
    assert isinstance(entities, Entities) and isinstance(relationships, Relationships)
    chars = entities.document.chars
    assert fetching == Progress(
        provider="gemini", stage="fetching", detail="Fetching the article"
    )
    assert pass1 == Progress(
        provider="gemini",
        stage="calling_model",
        detail=f"Pass 1 of 2: calling {spec.default_model} on {chars:,} characters",
        pass_number=1,
        pass_count=2,
    )
    assert pass2 == Progress(
        provider="gemini",
        stage="calling_model",
        detail=f"Pass 2 of 2: calling {spec.default_model} for relationships",
        pass_number=2,
        pass_count=2,
    )
    assert entities.document == DocumentSummary(
        url=SHIPMENT_URL, title="Tracing one shipment — Tidemark", chars=chars
    )
    assert [e.id for e in entities.entities] == [f"e{n}" for n in range(1, 7)]
    by_id = {e.id: e for e in entities.entities}
    event = by_id["e5"]
    assert event == ProposedEntity(
        id="e5",
        label="Event",
        name="Arrival of the shipment in Karsk",
        sentence="The shipment arrived in Karsk in February 2024.",
        date="2024-02",
        place="Karsk",
    )
    assert entities.dropped == 0
    assert by_id["e2"].date is None and by_id["e2"].place is None
    participants = [
        r for r in relationships.relationships if r.type == "PARTICIPATED_IN"
    ]
    assert [(r.from_id, r.to_id) for r in participants] == [("e4", "e5"), ("e3", "e6")]
    assert all(by_id[r.to_id].label == "Event" for r in participants)
    assert all(by_id[r.from_id].label != "Event" for r in participants)
    assert relationships.relationships[0] == ProposedRelationship(
        type="PARTICIPATED_IN",
        from_id="e4",
        to_id="e5",
        date_from="2024-02",
        date_to=None,
        date_precision="month",
        sentence="The shipment arrived in Karsk in February 2024.",
    )
    assert relationships.dropped == 0
    assert relationships.model == spec.default_model


def test_two_pass_events_each_pass_gets_its_own_prompt_and_the_whole_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pass 1 sees only the article; Pass 2 sees the stamped ids and names and
    the article; both are sent with the system prompt and the fixed budget."""
    network = _shipment_network()

    _extract_command(network, capsys, monkeypatch, "--url", SHIPMENT_URL)

    first, second = (orjson.loads(r.content) for r in network.chat_requests)
    for body in (first, second):
        assert body["max_tokens"] == PASS1_MAX_TOKENS
        system, user = body["messages"]
        assert (
            system["role"] == "system" and "Pass 2 — Relationships" in system["content"]
        )
        assert "shipment of dual-use machine tools" in user["content"]
    assert first["messages"][1]["content"].startswith("PASS: 1")
    assert '"id"' not in first["messages"][1]["content"]
    pass2 = second["messages"][1]["content"]
    assert pass2.startswith("PASS: 2")
    assert '{"id":"e1","label":"Location","name":"Port Verrin"}' in pass2
    assert (
        '{"id":"e5","label":"Event","name":"Arrival of the shipment in Karsk",'
        '"date":"2024-02","place":"Karsk"}'
    ) in pass2


def test_two_pass_events_the_text_file_is_the_document_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--text-file` reads the file, fetches nothing, and reports a Document
    with no URL and no title."""
    text = "The shipment arrived in Karsk in February 2024. " * 20
    path = tmp_path / "body.txt"
    path.write_text(text, encoding="utf-8")
    network = _shipment_network()

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--text-file", str(path)
    )

    assert code == 0
    assert [m.kind for m in messages] == [
        "progress",
        "entities",
        "progress",
        "relationships",
    ]
    entities = messages[1]
    assert isinstance(entities, Entities)
    assert entities.document == DocumentSummary(url=None, title=None, chars=len(text))
    assert [r.url.host for r in network.requests] == [PROVIDERS["gemini"].host] * 2
    first = orjson.loads(network.chat_requests[0].content)
    assert f"<<<BODY>>>\n{text}\n<<<END BODY>>>" in first["messages"][1]["content"]


def test_two_pass_events_a_relationship_with_an_unknown_id_is_dropped_and_counted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unknown ids are not a failure: the rest are reported and the count is in
    the `relationships` message."""
    stray = {**SHIPMENT_LINKS[0], "from_id": "e99"}
    network = Network(
        _json_reply(entities=SHIPMENT_ENTITIES),
        _json_reply(
            relationships=[stray, *SHIPMENT_LINKS, {**stray, "to_id": "Karsk"}]
        ),
        page="shipment_trace",
    )

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 0
    final = messages[-1]
    assert isinstance(final, Relationships)
    assert len(final.relationships) == 3
    assert final.dropped == 2
    assert {r.from_id for r in final.relationships} <= {f"e{n}" for n in range(1, 7)}


def test_two_pass_events_a_run_with_no_entities_still_makes_both_calls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An article with nothing to extract is a valid, empty result."""
    network = Network(
        _json_reply(entities=[]), _json_reply(relationships=[]), page="shipment_trace"
    )

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 0
    assert len(network.chat_requests) == 2
    entities, relationships = messages[2], messages[-1]
    assert isinstance(entities, Entities) and entities.entities == []
    assert isinstance(relationships, Relationships)
    assert relationships.relationships == [] and relationships.dropped == 0


def test_two_pass_events_a_429_in_pass_2_reports_which_pass_is_waiting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wait is `waiting_rate_limit` and still says "pass 2 of 2"; the
    retry is the same Pass 2 call, so the run still makes only two replies."""
    network = Network(
        _json_reply(entities=SHIPMENT_ENTITIES),
        httpx.Response(429),
        _json_reply(relationships=SHIPMENT_LINKS),
        page="shipment_trace",
    )
    sleeps: list[float] = []

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL, sleeps=sleeps
    )

    assert code == 0
    waits = [
        m
        for m in messages
        if isinstance(m, Progress) and m.stage == "waiting_rate_limit"
    ]
    assert [(w.pass_number, w.pass_count) for w in waits] == [(2, 2)]
    assert waits[0].detail == "Rate limited; retrying in 5 s (attempt 2 of 6)"
    assert sleeps == [5.0]
    assert isinstance(messages[-1], Relationships)


def test_two_pass_events_a_bad_pass_1_reply_stops_after_one_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pass 2 is not asked about entities that were never read."""
    network = Network(_chat_reply("I found nothing."), page="shipment_trace")

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 1
    assert len(network.chat_requests) == 1
    assert [m.kind for m in messages] == ["progress", "progress", "failed"]
    assert isinstance(messages[-1], Failed) and messages[-1].reason == "bad_output"


@pytest.mark.parametrize(
    "reply",
    ["No relationships.", '{"relationships": "none"}', '{"relationships'],
)
def test_two_pass_events_a_bad_pass_2_reply_fails_after_the_entities_were_sent(
    reply: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The `entities` message already went out; the run then ends `failed`
    with `bad_output`, so the last final message is the failure."""
    network = Network(
        _json_reply(entities=SHIPMENT_ENTITIES),
        _chat_reply(reply),
        page="shipment_trace",
    )

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 1
    assert [m.kind for m in messages] == [
        "progress",
        "progress",
        "entities",
        "progress",
        "failed",
    ]
    final = messages[-1]
    assert isinstance(final, Failed) and final.reason == "bad_output"
    assert "relationship" in final.message or "JSON" in final.message


def test_two_pass_events_a_provider_failure_in_pass_2_is_relayed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 401 on the second call is `auth`, without the key or the raw body."""
    network = Network(
        _json_reply(entities=SHIPMENT_ENTITIES),
        httpx.Response(401, text=f"bad key {KEY} RAW-BODY"),
        page="shipment_trace",
    )

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 1
    final = messages[-1]
    assert isinstance(final, Failed) and final.reason == "auth"
    assert "RAW-BODY" not in final.message


def test_two_pass_events_an_article_that_cannot_be_fetched_fails_before_any_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 404 is `fetch` and the Provider is not called."""
    network = _shipment_network()
    network.article_status = 404

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 1
    assert [m.kind for m in messages] == ["progress", "failed"]
    assert isinstance(messages[-1], Failed) and messages[-1].reason == "fetch"
    assert network.chat_requests == []


def test_two_pass_events_a_text_file_that_cannot_be_read_fails_before_any_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing, empty, blank or non-UTF-8 file is `fetch` with a message that
    names the file and no Model call."""
    blank = tmp_path / "blank.txt"
    blank.write_text(" \n\t", encoding="utf-8")
    binary = tmp_path / "binary.txt"
    binary.write_bytes(b"\xff\xfe\x00bad")
    for path in (tmp_path / "missing.txt", blank, binary, tmp_path):
        network = _shipment_network()

        code, messages = _extract_command(
            network, capsys, monkeypatch, "--text-file", str(path)
        )

        assert code == 1
        (failed,) = messages
        assert isinstance(failed, Failed) and failed.reason == "fetch"
        assert path.name in failed.message
        assert network.requests == []


def test_two_pass_events_without_a_key_nothing_is_requested(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No key is `auth`; neither the article nor the Provider is asked."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    network = _shipment_network()

    code = main(
        ["extract", "--provider", "gemini", "--url", SHIPMENT_URL],
        transport=network.transport,
    )

    (failed,) = _lines(capsys.readouterr().out)
    assert code == 1
    assert isinstance(failed, Failed) and failed.reason == "auth"
    assert network.requests == []


def test_the_article_site_never_sees_the_key_in_extract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The article request carries no Authorization header."""
    network = _shipment_network()

    _extract_command(network, capsys, monkeypatch, "--url", SHIPMENT_URL)

    (article_request,) = [r for r in network.requests if r.url.host == "news.example"]
    assert "authorization" not in article_request.headers
    assert KEY not in str(article_request.url)


@pytest.mark.parametrize(
    "argv",
    [
        ["extract", "--provider", "gemini"],
        ["extract", "--url", SHIPMENT_URL],
        ["extract", "--provider", "gemini", "--url", SHIPMENT_URL, "--text-file", "x"],
        ["extract", "--provider", "nowhere", "--url", SHIPMENT_URL],
        ["extract", "--provider", "gemini", "--url"],
    ],
    ids=["no-source", "no-provider", "both-sources", "unknown-provider", "no-value"],
)
def test_extract_needs_a_provider_and_exactly_one_source(argv: list[str]) -> None:
    """argparse refuses all of these."""
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2


def test_test_extraction_still_reports_a_single_pass_without_pass_numbers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The old command is one call, and its progress carries no pass numbers."""
    network = Network(_chat_reply(REPLY), page="campaign_documents")

    code, messages = _extract(network, capsys, monkeypatch)

    assert code == 0
    assert len(network.chat_requests) == 1
    progress = [m for m in messages if isinstance(m, Progress)]
    assert [(p.pass_number, p.pass_count) for p in progress] == [(None, None)] * 2


# ---- one malformed item (ISC-63) ----

GARBLED_ENTITY = {
    "label": "Person",
    "name": "Mr Garble",
    "\u0436supporting_sentences": ["Mr Garble signed the order."],
}


def test_malformed_item_in_pass_1_still_yields_the_rest_and_runs_pass_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bad entity is dropped and counted in `entities`; the other six keep
    contiguous ids, and Pass 2 runs over them."""
    network = Network(
        _json_reply(
            entities=[SHIPMENT_ENTITIES[0], GARBLED_ENTITY, *SHIPMENT_ENTITIES[1:]]
        ),
        _json_reply(relationships=SHIPMENT_LINKS),
        page="shipment_trace",
    )

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 0
    entities = next(m for m in messages if isinstance(m, Entities))
    assert [e.id for e in entities.entities] == [f"e{n}" for n in range(1, 7)]
    assert "Mr Garble" not in [e.name for e in entities.entities]
    assert entities.dropped == 1
    assert len(network.chat_requests) == 2
    pass2 = orjson.loads(network.chat_requests[1].content)["messages"][1]["content"]
    assert '"id":"e6"' in pass2 and '"id":"e7"' not in pass2 and "Garble" not in pass2
    final = messages[-1]
    assert isinstance(final, Relationships)
    assert len(final.relationships) == 3 and final.dropped == 0


def test_malformed_item_in_pass_2_is_counted_with_the_unknown_ids(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A garbled relationship, an unknown id and a participant link that does
    not end at an Event are all in the one `dropped`; the rest come through."""
    garbled = {
        "\u0436type": "OWNS",
        **{k: v for k, v in SHIPMENT_LINKS[0].items() if k != "type"},
    }
    unknown = {**SHIPMENT_LINKS[0], "from_id": "e99"}
    wrong_end = {**SHIPMENT_LINKS[0], "to_id": "e2"}
    network = Network(
        _json_reply(entities=SHIPMENT_ENTITIES),
        _json_reply(relationships=[garbled, *SHIPMENT_LINKS, unknown, wrong_end]),
        page="shipment_trace",
    )

    code, messages = _extract_command(
        network, capsys, monkeypatch, "--url", SHIPMENT_URL
    )

    assert code == 0
    final = messages[-1]
    assert isinstance(final, Relationships)
    assert len(final.relationships) == 3
    assert final.dropped == 3


def test_malformed_item_in_test_extraction_is_dropped_and_counted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The old command shares the Pass 1 parser, so it keeps the rest too."""
    network = Network(
        _json_reply(entities=[GARBLED_ENTITY, *SHIPMENT_ENTITIES[:2]]),
        page="shipment_trace",
    )

    code, messages = _extract(network, capsys, monkeypatch)

    final = messages[-1]
    assert code == 0 and isinstance(final, Entities)
    assert [e.id for e in final.entities] == ["e1", "e2"]
    assert final.dropped == 1


def test_malformed_item_reaches_stderr_with_no_logging_set_up() -> None:
    """The worker configures no logging, yet the error line for a dropped item
    reaches stderr, which the server logs, and holds none of the reply."""
    reply = orjson.dumps({"entities": [GARBLED_ENTITY]}).decode()
    code = (
        "import sys; from respec_worker.extraction import parse_pass1; "
        f"parse_pass1({reply!r})"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert b"missing" in result.stderr and b"supporting_sentences" in result.stderr
    assert b"Garble" not in result.stderr
    assert "\u0436".encode() not in result.stderr
    assert result.stdout == b""
