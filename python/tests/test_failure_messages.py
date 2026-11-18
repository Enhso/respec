"""ISC-53: every failure reason maps to one plain message with a next step, from
one table, and the client builds its failures from that table."""

from datetime import UTC, datetime, timedelta
from typing import get_args

import httpx
import orjson
import pytest

from respec_worker.client import ChatFailure, chat
from respec_worker.messages import (
    FAILURE_TEXT,
    Reason,
    describe_reset,
    failure_message,
)
from respec_worker.providers import PROVIDERS, Provider

KEY = "secret-key-0123456789"
NOW = datetime(2026, 11, 18, 11, 0, 0, tzinfo=UTC)
MESSAGES = [{"role": "user", "content": "ping"}]


def test_failure_messages_the_table_names_exactly_the_reasons() -> None:
    """A new reason without a message, or a message for a reason that does not
    exist, fails here."""
    assert FAILURE_TEXT.keys() == set(get_args(Reason))


@pytest.mark.parametrize("reason", get_args(Reason))
def test_failure_messages_each_reason_has_one_plain_sentence_with_a_next_step(
    reason: Reason,
) -> None:
    entry = FAILURE_TEXT[reason]
    message = failure_message(
        reason, "gemini", "some-model", retry_at=NOW + timedelta(hours=5), now=NOW
    )

    assert entry.what.strip() and entry.next_step.strip()
    assert message.endswith(".") and ". " not in message
    assert "{" not in message and "}" not in message
    assert message.startswith(entry.what.split("{")[0])
    next_step = entry.next_step.split("{")[0].strip()
    assert next_step in message
    assert message.index(next_step) > message.index(";")


@pytest.mark.parametrize("provider", get_args(Provider))
@pytest.mark.parametrize(
    "reason",
    ["auth", "quota", "daily_quota", "rate_limit", "overloaded", "network", "other"],
)
def test_failure_messages_a_provider_failure_names_the_provider(
    reason: Reason, provider: Provider
) -> None:
    message = failure_message(reason, provider, "some-model", now=NOW)

    assert PROVIDERS[provider].label in message


@pytest.mark.parametrize("reason", ["daily_quota", "model_unavailable"])
def test_failure_messages_a_model_failure_names_the_model(reason: Reason) -> None:
    assert "some-model" in failure_message(reason, "gemini", "some-model", now=NOW)


def test_failure_messages_a_daily_quota_says_when_to_try_again() -> None:
    message = failure_message(
        "daily_quota", "gemini", "m", retry_at=NOW + timedelta(hours=5), now=NOW
    )

    assert "try again after 2026-11-18 16:00 UTC (in about 5 hours)." in message


def test_failure_messages_a_daily_quota_without_a_time_names_the_usual_reset() -> None:
    message = failure_message("daily_quota", "gemini", "m", now=NOW)

    assert "try again once the quota resets, which is usually within a day." in message


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-30, "under an hour"),
        (3599, "under an hour"),
        (3600, "about 1 hour"),
        (5399, "about 1 hour"),
        (5400, "about 2 hours"),
        (82606, "about 23 hours"),
    ],
)
def test_failure_messages_how_far_away_the_reset_is(
    seconds: int, expected: str
) -> None:
    text = describe_reset(NOW + timedelta(seconds=seconds), NOW)

    assert text.endswith(f"(in {expected})")


def _chat_failure(response: httpx.Response | Exception) -> ChatFailure:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    with pytest.raises(ChatFailure) as excinfo:
        chat(
            "gemini",
            KEY,
            "some-model",
            MESSAGES,
            transport=httpx.MockTransport(handler),
            now=lambda: NOW,
        )
    return excinfo.value


def _reply(**choice: object) -> httpx.Response:
    return httpx.Response(200, json={"choices": [choice]})


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(401, text="no"), "auth"),
        (httpx.Response(402, text="pay"), "quota"),
        (httpx.Response(404, text="gone"), "model_unavailable"),
        (httpx.Response(429, text="slow"), "rate_limit"),
        (httpx.Response(503, text="busy"), "overloaded"),
        (httpx.Response(500, text="oops"), "other"),
        (_reply(message={"content": " "}), "other"),
        (
            _reply(message={"content": "{"}, finish_reason="length"),
            "output_truncated",
        ),
        (httpx.ConnectError(f"refused {KEY}"), "network"),
    ],
)
def test_failure_messages_the_client_gives_the_tables_message(
    response: httpx.Response | Exception, reason: Reason
) -> None:
    """The text of each failure the client builds is the table's, not its own."""
    failure = _chat_failure(response)

    assert failure.reason == reason
    assert failure.message == failure_message(reason, "gemini", "some-model", now=NOW)


def test_failure_messages_a_daily_quota_from_the_client_is_the_tables_too() -> None:
    body = orjson.dumps(
        {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaId": "RequestsPerDay"}],
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "7200s",
                    },
                ]
            }
        }
    )

    failure = _chat_failure(httpx.Response(429, content=body))

    assert failure.retry_at == NOW + timedelta(hours=2)
    assert failure.message == failure_message(
        "daily_quota", "gemini", "some-model", retry_at=failure.retry_at, now=NOW
    )
