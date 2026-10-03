"""The chat client: replies, how each failure maps to a reason, and retries."""

import math

import httpx
import orjson
import pytest

from respec_worker.client import (
    LONG_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    ChatFailure,
    chat,
    chat_with_retries,
)
from respec_worker.messages import Reason

KEY = "secret-key-0123456789"
BODY_MARKER = "RAW-BODY-MARKER"
MESSAGES = [{"role": "user", "content": "ping"}]


def _answering(response: httpx.Response) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: response)


def _failing_with(error: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return httpx.MockTransport(handler)


def _fail(transport: httpx.MockTransport) -> ChatFailure:
    with pytest.raises(ChatFailure) as excinfo:
        chat("openrouter", KEY, "some-model", MESSAGES, transport=transport)
    return excinfo.value


def test_chat_sends_the_request_and_returns_the_trimmed_reply() -> None:
    """The body carries model, messages and a small max_tokens."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": " pong\n"}}]}
        )

    reply = chat(
        "gemini",
        KEY,
        "some-model",
        MESSAGES,
        32,
        transport=httpx.MockTransport(handler),
    )

    assert reply == "pong"
    (request,) = seen
    assert request.method == "POST"
    assert orjson.loads(request.content) == {
        "model": "some-model",
        "messages": MESSAGES,
        "max_tokens": 32,
    }


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (401, "unauthorised", "auth"),
        (403, "forbidden", "auth"),
        (
            400,
            '{"error": {"message": "API key not valid. Please pass a valid API key."}}',
            "auth",
        ),
        (402, "payment required", "quota"),
        (429, "slow down", "rate_limit"),
        (404, "no such model", "model_unavailable"),
        (400, "the request body is malformed", "other"),
        (500, "internal error", "other"),
        (503, "overloaded", "other"),
    ],
)
def test_status_maps_to_its_reason_without_leaking_the_body(
    status: int, body: str, reason: Reason
) -> None:
    """Each status class gives its reason, and the message is one plain
    sentence holding neither the raw body nor the key."""
    echoed = f"{body} {BODY_MARKER} {KEY}"

    failure = _fail(_answering(httpx.Response(status, text=echoed)))

    assert failure.reason == reason
    assert failure.message == str(failure)
    assert BODY_MARKER not in failure.message
    assert KEY not in failure.message
    assert failure.message.endswith(".")


def test_rate_limit_message_says_to_try_again_later() -> None:
    """The 429 message names the rate limit or a quota and gives the next step."""
    message = _fail(_answering(httpx.Response(429))).message
    assert "rate limit or a quota was reached" in message
    assert "try again later" in message


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("took too long"),
        httpx.ConnectTimeout("took too long"),
        httpx.ConnectError(f"connection refused, {KEY}"),
    ],
)
def test_timeout_and_connection_errors_map_to_network(error: Exception) -> None:
    """Timeouts and connection errors are `network`, with the error text unshown."""
    failure = _fail(_failing_with(error))
    assert failure.reason == "network"
    assert KEY not in failure.message


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}),
    ],
)
def test_an_answer_without_reply_text_is_other(response: httpx.Response) -> None:
    """A 200 with no usable reply text is a failure, not an empty success."""
    assert _fail(_answering(response)).reason == "other"


def test_timeout_is_sixty_seconds() -> None:
    """The client sets a 60 s timeout on the request."""
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    chat("openrouter", KEY, "m", MESSAGES, transport=httpx.MockTransport(handler))

    assert seen == [{"connect": 60.0, "read": 60.0, "write": 60.0, "pool": 60.0}]


def test_chat_takes_a_longer_read_timeout_but_connects_within_sixty_seconds() -> None:
    """A real-sized pass may wait minutes for the reply, not for the connection."""
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    chat(
        "openrouter",
        KEY,
        "m",
        MESSAGES,
        timeout=LONG_TIMEOUT_SECONDS,
        transport=httpx.MockTransport(handler),
    )

    assert LONG_TIMEOUT_SECONDS == 600.0
    assert seen == [{"connect": 60.0, "read": 600.0, "write": 600.0, "pool": 600.0}]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("7", 7.0),
        ("0.5", 0.5),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("-3", None),
        ("nan", None),
        ("inf", None),
        ("", None),
    ],
)
def test_a_429_carries_the_retry_after_it_gave(
    header: str, expected: float | None
) -> None:
    """Only a plain number of seconds counts as Retry-After."""
    headers = {"Retry-After": header} if header else {}

    failure = _fail(_answering(httpx.Response(429, headers=headers)))

    assert failure.reason == "rate_limit"
    assert failure.retry_after == expected


def _sequence(*responses: httpx.Response) -> tuple[httpx.MockTransport, list[int]]:
    """A transport that answers with each response in turn, and a request count."""
    queue = list(responses)
    count: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        count.append(1)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return httpx.MockTransport(handler), count


def _retrying(
    transport: httpx.MockTransport,
) -> tuple[list[float], list[tuple[int, int, int]]]:
    """Run `chat_with_retries` and return the sleeps and the `on_wait` calls."""
    sleeps: list[float] = []
    waits: list[tuple[int, int, int]] = []
    chat_with_retries(
        "openrouter",
        KEY,
        "m",
        MESSAGES,
        100,
        on_wait=lambda *args: waits.append(args),
        transport=transport,
        sleep=sleeps.append,
    )
    return sleeps, waits


OK = httpx.Response(200, json={"choices": [{"message": {"content": "fine"}}]})


def test_two_429s_then_a_reply_wait_five_then_ten_seconds() -> None:
    """The default backoff starts at 5 s and doubles; each wait is announced
    before it starts, naming the attempt that follows it."""
    transport, count = _sequence(httpx.Response(429), httpx.Response(429), OK)

    sleeps, waits = _retrying(transport)

    assert len(count) == 3
    assert sleeps == [5.0, 10.0]
    assert waits == [(5, 2, 6), (10, 3, 6)]


def test_the_reply_is_returned_after_the_retries() -> None:
    """`chat_with_retries` returns the text of the call that finally worked."""
    transport, _ = _sequence(httpx.Response(429), OK)

    reply = chat_with_retries(
        "gemini",
        KEY,
        "m",
        MESSAGES,
        100,
        on_wait=lambda *args: None,
        transport=transport,
        sleep=lambda seconds: None,
    )

    assert reply == "fine"


def test_a_429_on_every_attempt_gives_up_after_six_with_rate_limit() -> None:
    """Six attempts, the five documented waits, then the `rate_limit` failure."""
    transport, count = _sequence(httpx.Response(429))
    sleeps: list[float] = []
    waits: list[tuple[int, int, int]] = []

    with pytest.raises(ChatFailure) as excinfo:
        chat_with_retries(
            "openrouter",
            KEY,
            "m",
            MESSAGES,
            100,
            on_wait=lambda *args: waits.append(args),
            transport=transport,
            sleep=sleeps.append,
        )

    assert MAX_ATTEMPTS == 6
    assert excinfo.value.reason == "rate_limit"
    assert len(count) == 6
    assert sleeps == [5.0, 10.0, 20.0, 40.0, 60.0]
    assert waits == [(5, 2, 6), (10, 3, 6), (20, 4, 6), (40, 5, 6), (60, 6, 6)]


@pytest.mark.parametrize(
    ("retry_after", "slept"),
    [("20", 20.0), ("7.5", 7.5), ("0", 1.0), ("3600", 120.0)],
)
def test_retry_after_is_honoured_within_one_and_one_hundred_twenty_seconds(
    retry_after: str, slept: float
) -> None:
    """A Retry-After replaces the default wait, kept inside the bounds."""
    transport, _ = _sequence(
        httpx.Response(429, headers={"Retry-After": retry_after}), OK
    )

    sleeps, waits = _retrying(transport)

    assert sleeps == [slept]
    assert waits == [(math.ceil(slept), 2, 6)]


@pytest.mark.parametrize("status", [400, 401, 402, 404, 500])
def test_only_a_429_is_retried(status: int) -> None:
    """Any other failure is raised at once, with no wait."""
    transport, count = _sequence(httpx.Response(status), OK)
    sleeps: list[float] = []

    with pytest.raises(ChatFailure):
        chat_with_retries(
            "openrouter",
            KEY,
            "m",
            MESSAGES,
            100,
            on_wait=lambda *args: None,
            transport=transport,
            sleep=sleeps.append,
        )

    assert len(count) == 1
    assert sleeps == []


def test_chat_with_retries_uses_the_long_timeout_by_default() -> None:
    """A real-sized pass is not cut off at the test call's 60 s."""
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return OK

    chat_with_retries(
        "openrouter",
        KEY,
        "m",
        MESSAGES,
        100,
        on_wait=lambda *args: None,
        transport=httpx.MockTransport(handler),
    )

    assert seen == [600.0]
