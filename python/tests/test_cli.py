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
                label="Organization",
                name="State Review Directorate",
                sentence="A source inside the State Review Directorate left.",
            ),
            ProposedEntity(
                label="Person",
                name="Ada Verrin",
                sentence="Ada Verrin signed the order.",
            ),
        ],
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
    ["I found no entities.", '{"entities": [{"label": "Dog"}]}', '{"entities'],
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
