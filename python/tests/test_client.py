"""The chat client: replies, and how each failure maps to a reason."""

import httpx
import orjson
import pytest

from respec_worker.client import ChatFailure, chat
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
