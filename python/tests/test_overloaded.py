"""An overloaded Provider (HTTP 503) is retried like a per-minute 429 but shown
as its own wait, and an OpenRouter 403 for one gated Model is not a bad key."""

import httpx
import orjson
import pytest
from test_cli import ARTICLES, REPLY, SHIPMENT_ENTITIES, SHIPMENT_LINKS, Network

from respec_worker.cli import main
from respec_worker.client import MAX_ATTEMPTS, ChatFailure, chat, chat_with_retries
from respec_worker.messages import (
    MESSAGE_ADAPTER,
    AnyMessage,
    Entities,
    Failed,
    Progress,
    Reason,
)
from respec_worker.providers import Provider

KEY = "secret-key-0123456789"
MESSAGES = [{"role": "user", "content": "ping"}]
OK = httpx.Response(200, json={"choices": [{"message": {"content": "fine"}}]})
GATE = (ARTICLES.parent / "errors" / "openrouter_403_gate.json").read_bytes()
ARTICLE_URL = "https://news.example/articles/42"


def _chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _fail(provider: Provider, response: httpx.Response) -> ChatFailure:
    transport = httpx.MockTransport(lambda request: response)
    with pytest.raises(ChatFailure) as excinfo:
        chat(provider, KEY, "some-model", MESSAGES, transport=transport)
    return excinfo.value


def _retrying(
    *responses: httpx.Response,
) -> tuple[list[float], list[tuple[int, int, int, Reason]], int, ChatFailure | None]:
    """Run `chat_with_retries` over the responses in turn (the last repeats);
    return the sleeps, the `on_wait` calls, the request count and the failure."""
    queue = list(responses)
    count: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        count.append(1)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    sleeps: list[float] = []
    waits: list[tuple[int, int, int, Reason]] = []
    failure = None
    try:
        chat_with_retries(
            "gemini",
            KEY,
            "m",
            MESSAGES,
            100,
            on_wait=lambda *args: waits.append(args),
            transport=httpx.MockTransport(handler),
            sleep=sleeps.append,
        )
    except ChatFailure as err:
        failure = err
    return sleeps, waits, len(count), failure


# ---- 503 ----


def test_overloaded_a_503_is_its_own_reason_and_names_the_provider() -> None:
    failure = _fail("gemini", httpx.Response(503, text="high demand RAW-BODY"))

    assert failure.reason == "overloaded"
    assert "Google Gemini" in failure.message and "overloaded" in failure.message
    assert "RAW-BODY" not in failure.message


def test_overloaded_two_503s_then_a_reply_wait_as_a_rate_limit_would() -> None:
    """The backoff is the 429's: 5 s, then 10 s, each announced as `overloaded`."""
    sleeps, waits, requests, failure = _retrying(
        httpx.Response(503), httpx.Response(503), OK
    )

    assert failure is None and requests == 3
    assert sleeps == [5.0, 10.0]
    assert waits == [(5, 2, 6, "overloaded"), (10, 3, 6, "overloaded")]


def test_overloaded_every_attempt_gives_up_after_six_with_overloaded() -> None:
    sleeps, waits, requests, failure = _retrying(httpx.Response(503))

    assert failure is not None and failure.reason == "overloaded"
    assert requests == MAX_ATTEMPTS == 6
    assert sleeps == [5.0, 10.0, 20.0, 40.0, 60.0]
    assert [reason for *_, reason in waits] == ["overloaded"] * 5


def test_overloaded_a_503_honours_retry_after_like_a_429() -> None:
    sleeps, _, _, _ = _retrying(httpx.Response(503, headers={"Retry-After": "30"}), OK)

    assert sleeps == [30.0]


def test_overloaded_a_429_then_a_503_then_a_reply_names_each_wait() -> None:
    _, waits, _, failure = _retrying(httpx.Response(429), httpx.Response(503), OK)

    assert failure is None
    assert [reason for *_, reason in waits] == ["rate_limit", "overloaded"]


# ---- 403 ----


def test_overloaded_openrouter_403_for_a_gated_model_is_model_unavailable() -> None:
    """The real body of a Model that is only offered to agentic harnesses."""
    failure = _fail("openrouter", httpx.Response(403, content=GATE))

    assert failure.reason == "model_unavailable"
    assert "OpenRouter" in failure.message and "some-model" in failure.message
    assert "key" not in failure.message


@pytest.mark.parametrize(
    ("provider", "status", "body"),
    [
        ("openrouter", 403, b'{"error": {"message": "Your API key is disabled"}}'),
        ("openrouter", 403, b"forbidden"),
        ("openrouter", 403, b'{"error": {"message": ""}}'),
        ("openrouter", 401, GATE),
        ("gemini", 403, GATE),
        ("gemini", 403, b'{"error": {"message": "Permission denied"}}'),
    ],
)
def test_overloaded_every_other_401_or_403_stays_auth(
    provider: Provider, status: int, body: bytes
) -> None:
    failure = _fail(provider, httpx.Response(status, content=body))

    assert failure.reason == "auth"


# ---- through the worker commands ----


def _messages(capsys: pytest.CaptureFixture[str]) -> list[AnyMessage]:
    out = capsys.readouterr().out
    return [MESSAGE_ADAPTER.validate_json(line) for line in out.splitlines()]


def test_overloaded_test_extraction_shows_waiting_overloaded_not_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two 503s then a reply: the page's progress says the Provider is
    overloaded, never "Rate limited"."""
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    network = Network(
        httpx.Response(503),
        httpx.Response(503),
        _chat_reply(REPLY),
        page="campaign_documents",
    )
    sleeps: list[float] = []

    code = main(
        ["test-extraction", "--provider", "gemini", "--url", ARTICLE_URL],
        transport=network.transport,
        sleep=sleeps.append,
    )

    messages = _messages(capsys)
    assert code == 0 and isinstance(messages[-1], Entities)
    waiting = [m for m in messages if isinstance(m, Progress) and "waiting" in m.stage]
    assert [(m.stage, m.detail) for m in waiting] == [
        (
            "waiting_overloaded",
            "Google Gemini is overloaded; retrying in 5 s (attempt 2 of 6)",
        ),
        (
            "waiting_overloaded",
            "Google Gemini is overloaded; retrying in 10 s (attempt 3 of 6)",
        ),
    ]
    assert sleeps == [5.0, 10.0]


def test_overloaded_extract_pass_2_waits_as_overloaded_and_still_says_pass_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    network = Network(
        _chat_reply(orjson.dumps({"entities": SHIPMENT_ENTITIES}).decode()),
        httpx.Response(503),
        _chat_reply(orjson.dumps({"relationships": SHIPMENT_LINKS}).decode()),
        page="shipment_trace",
    )

    code = main(
        ["extract", "--provider", "gemini", "--url", ARTICLE_URL],
        transport=network.transport,
        sleep=lambda seconds: None,
    )

    messages = _messages(capsys)
    assert code == 0
    (waiting,) = [
        m for m in messages if isinstance(m, Progress) and "waiting" in m.stage
    ]
    assert waiting.stage == "waiting_overloaded"
    assert (waiting.pass_number, waiting.pass_count) == (2, 2)
    assert "Rate limited" not in waiting.detail


def test_overloaded_extract_that_stays_overloaded_fails_with_overloaded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    network = Network(httpx.Response(503), page="campaign_documents")

    code = main(
        ["extract", "--provider", "gemini", "--url", ARTICLE_URL],
        transport=network.transport,
        sleep=lambda seconds: None,
    )

    final = _messages(capsys)[-1]
    assert code == 1
    assert isinstance(final, Failed) and final.reason == "overloaded"
    assert len(network.chat_requests) == 6
