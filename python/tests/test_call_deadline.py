"""A call has a total deadline, not only a limit on each read: a Provider that
keeps a connection open by trickling bytes still fails the call, as `network`,
so that the retry and fallback rules can start."""

from collections.abc import Iterator
from typing import get_args

import httpx
import orjson
import pytest

from respec_worker import client
from respec_worker.client import ChatFailure, chat, chat_with_retries
from respec_worker.providers import Provider

KEY = "secret-key-0123456789"
MESSAGES = [{"role": "user", "content": "ping"}]
REPLY = orjson.dumps({"choices": [{"message": {"content": "fine"}}]})


class FakeClock:
    """A clock that only the trickling stream moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Trickle(httpx.SyncByteStream):
    """A 200 body that arrives as ``chunks`` blank chunks, ``seconds`` apart on
    ``clock``, then ``tail``. ``sent`` counts the chunks the client has read."""

    def __init__(
        self, clock: FakeClock, seconds: float, chunks: int, tail: bytes = b""
    ) -> None:
        self.clock, self.seconds, self.chunks, self.tail = clock, seconds, chunks, tail
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        for _ in range(self.chunks):
            self.clock.now += self.seconds
            self.sent += 1
            yield b" "
        yield self.tail


def _transport(stream: Trickle) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))


@pytest.mark.parametrize("provider", get_args(Provider))
def test_deadline_a_call_that_trickles_past_it_fails_as_network(
    provider: Provider,
) -> None:
    """A chunk every 10 s: the deadline of 600 s passes at the 61st, and the
    client stops reading there instead of waiting for the other 939."""
    clock = FakeClock()
    stream = Trickle(clock, seconds=10, chunks=1000, tail=REPLY)

    with pytest.raises(ChatFailure) as excinfo:
        chat(
            provider,
            KEY,
            "m",
            MESSAGES,
            timeout=600.0,
            transport=_transport(stream),
            clock=clock,
        )

    assert excinfo.value.reason == "network"
    assert stream.sent == 61
    assert KEY not in excinfo.value.message


def test_deadline_a_slow_call_that_finishes_inside_it_is_read_whole() -> None:
    clock = FakeClock()
    stream = Trickle(clock, seconds=10, chunks=50, tail=REPLY)

    reply = chat(
        "gemini",
        KEY,
        "m",
        MESSAGES,
        timeout=600.0,
        transport=_transport(stream),
        clock=clock,
    )

    assert reply == "fine"
    assert stream.sent == 50


def test_deadline_counts_from_the_start_of_the_call() -> None:
    """Time before the first byte counts too: the first chunk comes 590 s in
    and 20 s after that, so it is already late."""
    clock = FakeClock()
    streams: list[Trickle] = []

    def handler(request: httpx.Request) -> httpx.Response:
        clock.now += 590
        streams.append(Trickle(clock, seconds=20, chunks=5, tail=REPLY))
        return httpx.Response(200, stream=streams[-1])

    with pytest.raises(ChatFailure) as excinfo:
        chat(
            "gemini",
            KEY,
            "m",
            MESSAGES,
            timeout=600.0,
            transport=httpx.MockTransport(handler),
            clock=clock,
        )

    assert excinfo.value.reason == "network"
    assert streams[0].sent == 1


def test_deadline_error_bodies_are_still_read_and_classified() -> None:
    """The body is read through the same loop, so a 503 is still `overloaded`
    and a 429 keeps its Retry-After."""
    clock = FakeClock()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, headers={"Retry-After": "30"}, text="busy")
    )

    with pytest.raises(ChatFailure) as excinfo:
        chat("gemini", KEY, "m", MESSAGES, transport=transport, clock=clock)

    assert excinfo.value.reason == "overloaded"
    assert excinfo.value.retry_after == 30.0


def test_deadline_a_call_past_it_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`network` is not a retried failure, so the next step is the fallback, not
    five more waits and six more deadlines."""
    clock = FakeClock()
    streams: list[Trickle] = []

    def handler(request: httpx.Request) -> httpx.Response:
        streams.append(Trickle(clock, seconds=100, chunks=1000, tail=REPLY))
        return httpx.Response(200, stream=streams[-1])

    real_chat = client.chat
    monkeypatch.setattr(
        client, "chat", lambda *args, **kwargs: real_chat(*args, clock=clock, **kwargs)
    )
    sleeps: list[float] = []

    with pytest.raises(ChatFailure) as excinfo:
        chat_with_retries(
            "gemini",
            KEY,
            "m",
            MESSAGES,
            100,
            on_wait=lambda *args: None,
            timeout=600.0,
            transport=httpx.MockTransport(handler),
            sleep=sleeps.append,
        )

    assert excinfo.value.reason == "network"
    assert len(streams) == 1 and sleeps == []
