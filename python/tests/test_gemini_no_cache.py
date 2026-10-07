"""No request to the Gemini Provider carries a prompt-caching directive (ISC-54).

Specter's cached-content calls hit a zero quota on Gemini, so the worker's
client must never send a cache directive there. Both entry points are checked
by walking every key in the captured request body, at any depth.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import orjson

from respec_worker.client import chat, chat_with_retries

KEY = "secret-key-0123456789"
MESSAGES = [{"role": "user", "content": "ping"}]
OK = httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})


def _keys(node: Any) -> Iterator[str]:
    """Every dict key in a parsed JSON value, nested at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def _assert_no_cache_keys(body: bytes) -> None:
    """Fail on the first key that mentions 'cache' in any letter case."""
    for key in _keys(orjson.loads(body)):
        assert "cache" not in key.lower(), f"cache directive key sent: {key}"


def test_gemini_no_cache_through_chat() -> None:
    """A chat request to Gemini has no 'cache' key anywhere in its body."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return OK

    reply = chat(
        "gemini",
        KEY,
        "gemini-flash-lite-latest",
        MESSAGES,
        transport=httpx.MockTransport(handler),
    )

    assert reply == "pong"
    (request,) = seen
    _assert_no_cache_keys(request.content)


def test_gemini_no_cache_through_chat_with_retries() -> None:
    """A chat_with_retries request to Gemini has no 'cache' key in its body."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return OK

    reply = chat_with_retries(
        "gemini",
        KEY,
        "gemini-flash-lite-latest",
        MESSAGES,
        100,
        on_wait=lambda *args: None,
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )

    assert reply == "pong"
    (request,) = seen
    _assert_no_cache_keys(request.content)
