"""A per-day quota 429 is told apart from a per-minute one, is never retried, and
ends the run as `daily_quota` with the time the quota comes back (ISC-14.1)."""

from datetime import UTC, datetime, timedelta
from typing import get_args

import httpx
import orjson
import pytest
from test_cli import ARTICLES, Network

from respec_worker.cli import main
from respec_worker.client import ChatFailure, chat, chat_with_retries
from respec_worker.messages import MESSAGE_ADAPTER, AnyMessage, Failed, Progress
from respec_worker.providers import Provider

KEY = "secret-key-0123456789"
ERRORS = ARTICLES.parent / "errors"
MESSAGES = [{"role": "user", "content": "ping"}]
NOW = datetime(2026, 11, 18, 11, 0, 0, tzinfo=UTC)
PER_DAY_DELAY = timedelta(seconds=82606)
"""The `retryDelay` of the real Gemini body in `errors/gemini_429_per_day.json`."""

GEMINI_PER_DAY = (ERRORS / "gemini_429_per_day.json").read_bytes()


def _gemini_429(quota_id: str, retry_delay: str | None, *, wrapped: bool) -> bytes:
    """A Gemini 429 body shaped like the real per-day one, with another quota id
    and retry delay. Only the per-day body is a real capture."""
    details: list[dict[str, object]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id, "quotaValue": "5"}],
        }
    ]
    if retry_delay is not None:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": retry_delay,
            }
        )
    error = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return orjson.dumps([error] if wrapped else error)


def _openrouter_429(message: str, reset_ms: str | None) -> bytes:
    metadata: dict[str, object] = {}
    if reset_ms is not None:
        metadata["headers"] = {"X-RateLimit-Limit": "50", "X-RateLimit-Reset": reset_ms}
    return orjson.dumps(
        {"error": {"message": message, "code": 429, "metadata": metadata}}
    )


def _fail(provider: Provider, response: httpx.Response) -> ChatFailure:
    transport = httpx.MockTransport(lambda request: response)
    with pytest.raises(ChatFailure) as excinfo:
        chat(
            provider, KEY, "some-model", MESSAGES, transport=transport, now=lambda: NOW
        )
    return excinfo.value


def test_daily_quota_gemini_per_day_body_is_daily_quota_with_its_retry_time() -> None:
    """The real per-day body, wrapped in an array as the OpenAI-compatible
    endpoint wraps it: reason `daily_quota`, back at now plus the `retryDelay`,
    and a message that names the Provider and the time."""
    failure = _fail("gemini", httpx.Response(429, content=GEMINI_PER_DAY))

    assert failure.reason == "daily_quota"
    assert failure.retry_at == NOW + PER_DAY_DELAY
    assert "Google Gemini" in failure.message
    assert "2026-11-19 09:56 UTC" in failure.message
    assert "23 hours" in failure.message


@pytest.mark.parametrize("wrapped", [True, False])
def test_daily_quota_gemini_body_may_be_an_array_or_an_object(wrapped: bool) -> None:
    body = _gemini_429(
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "3600s", wrapped=wrapped
    )

    failure = _fail("gemini", httpx.Response(429, content=body))

    assert failure.reason == "daily_quota"
    assert failure.retry_at == NOW + timedelta(hours=1)


def test_daily_quota_gemini_without_a_retry_delay_still_says_so_and_has_no_time() -> (
    None
):
    body = _gemini_429(
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier", None, wrapped=True
    )

    failure = _fail("gemini", httpx.Response(429, content=body))

    assert failure.reason == "daily_quota"
    assert failure.retry_at is None
    assert "Google Gemini" in failure.message
    assert "daily quota" in failure.message


@pytest.mark.parametrize("wrapped", [True, False])
def test_a_per_minute_gemini_429_stays_a_rate_limit(wrapped: bool) -> None:
    """A quota id with `PerMinute` is the retried kind, even with a retry delay."""
    body = _gemini_429(
        "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "41s", wrapped=wrapped
    )

    failure = _fail("gemini", httpx.Response(429, content=body))

    assert failure.reason == "rate_limit"
    assert failure.retry_at is None


def test_daily_quota_openrouter_free_models_per_day_uses_its_reset_time() -> None:
    """The reset time is absolute, in epoch milliseconds. Source of this body:
    unconfirmed, built from OpenRouter's documented shape (no real capture yet)."""
    reset = datetime(2026, 11, 19, 0, 0, 0, tzinfo=UTC)
    body = _openrouter_429(
        "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock "
        "1000 free model requests per day",
        str(int(reset.timestamp() * 1000)),
    )

    failure = _fail("openrouter", httpx.Response(429, content=body))

    assert failure.reason == "daily_quota"
    assert failure.retry_at == reset
    assert "OpenRouter" in failure.message
    assert "2026-11-19 00:00 UTC" in failure.message


def test_an_openrouter_429_that_is_not_the_daily_limit_stays_a_rate_limit() -> None:
    body = _openrouter_429(
        "Provider returned error: rate-limited upstream", "1795046400000"
    )

    failure = _fail("openrouter", httpx.Response(429, content=body))

    assert failure.reason == "rate_limit"
    assert failure.retry_at is None


@pytest.mark.parametrize("body", [b"", b"slow down", b"[]", b'{"error": "text"}'])
@pytest.mark.parametrize("provider", get_args(Provider))
def test_a_429_with_no_readable_quota_detail_stays_a_rate_limit(
    provider: Provider, body: bytes
) -> None:
    failure = _fail(provider, httpx.Response(429, content=body))

    assert failure.reason == "rate_limit"


def test_a_daily_quota_is_not_retried() -> None:
    """One request, no wait, and the failure is raised for the caller."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, content=GEMINI_PER_DAY)

    sleeps: list[float] = []
    waits: list[tuple[object, ...]] = []

    with pytest.raises(ChatFailure) as excinfo:
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

    assert excinfo.value.reason == "daily_quota"
    assert len(seen) == 1
    assert sleeps == [] and waits == []


def test_a_daily_quota_message_holds_neither_the_key_nor_the_body() -> None:
    body = GEMINI_PER_DAY.replace(b"Quota exceeded", f"{KEY} RAW-BODY-MARKER".encode())

    failure = _fail("gemini", httpx.Response(429, content=body))

    assert failure.reason == "daily_quota"
    assert KEY not in failure.message and "RAW-BODY-MARKER" not in failure.message


# ---- through the worker commands ----


def _messages(capsys: pytest.CaptureFixture[str]) -> list[AnyMessage]:
    out = capsys.readouterr().out
    return [MESSAGE_ADAPTER.validate_json(line) for line in out.splitlines()]


def test_daily_quota_extract_ends_with_failed_and_the_retry_time_and_does_not_wait(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A per-day 429 in Pass 1: one chat request, no `waiting_rate_limit`
    message, no sleep, and a final `failed` that names the Provider and carries
    `retry_at` as a UTC time a day away."""
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    network = Network(
        httpx.Response(429, content=GEMINI_PER_DAY), page="campaign_documents"
    )
    sleeps: list[float] = []

    before = datetime.now(UTC)
    code = main(
        [
            "extract",
            "--provider",
            "gemini",
            "--url",
            "https://news.example/articles/42",
        ],
        transport=network.transport,
        sleep=sleeps.append,
    )
    after = datetime.now(UTC)

    messages = _messages(capsys)
    assert code == 1
    final = messages[-1]
    assert isinstance(final, Failed)
    assert (final.provider, final.reason) == ("gemini", "daily_quota")
    assert final.retry_at is not None
    assert (
        before + PER_DAY_DELAY
        <= final.retry_at
        <= after + PER_DAY_DELAY + timedelta(seconds=1)
    )
    assert final.retry_at.utcoffset() == timedelta(0)
    assert "Google Gemini" in final.message
    assert final.retry_at.strftime("%Y-%m-%d %H:%M UTC") in final.message
    assert not [m for m in messages if isinstance(m, Progress) and "waiting" in m.stage]
    assert len(network.chat_requests) == 1
    assert sleeps == []


def test_daily_quota_a_failed_message_is_one_json_line_with_retry_at_in_utc(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """On the wire `retry_at` is an ISO 8601 string ending in Z, or null."""
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    network = Network(
        httpx.Response(429, content=GEMINI_PER_DAY), page="campaign_documents"
    )

    main(
        [
            "test-extraction",
            "--provider",
            "gemini",
            "--url",
            "https://news.example/articles/42",
        ],
        transport=network.transport,
        sleep=lambda seconds: None,
    )

    raw = orjson.loads(capsys.readouterr().out.splitlines()[-1])
    assert raw["reason"] == "daily_quota"
    assert isinstance(raw["retry_at"], str) and raw["retry_at"].endswith("Z")
    datetime.fromisoformat(raw["retry_at"])


def test_daily_quota_test_call_reports_it_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(429, content=GEMINI_PER_DAY)
    )

    code = main(["test-call", "--provider", "gemini"], transport=transport)

    started, failed = _messages(capsys)
    assert code == 1
    assert isinstance(failed, Failed)
    assert failed.reason == "daily_quota" and failed.retry_at is not None


def test_a_failure_that_is_not_a_daily_quota_has_a_null_retry_at(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="no"))

    main(["test-call", "--provider", "gemini"], transport=transport)

    *_, failed = _messages(capsys)
    assert isinstance(failed, Failed) and failed.retry_at is None
