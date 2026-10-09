"""Respec's own small client for the Providers' OpenAI-compatible chat API.

``chat`` sends one chat request and returns the reply text, or raises a
``ChatFailure`` carrying a reason the operator can act on. It does not retry.
``chat_with_retries`` adds the per-minute rate-limit retries a long call needs.
"""

import math
import time
from collections.abc import Callable

import httpx
import orjson

from respec_worker.messages import Reason
from respec_worker.providers import PROVIDERS, Provider

TIMEOUT_SECONDS = 60.0
"""The default timeout: enough for a one-word reply."""

LONG_TIMEOUT_SECONDS = 600.0
"""The read timeout for a real-sized pass; the 2026-10-01 probe took 213 s."""

MAX_ATTEMPTS = 6
"""How many times ``chat_with_retries`` tries before giving up on a 429."""

RETRY_DELAYS = (5.0, 10.0, 20.0, 40.0, 60.0)
"""Seconds to wait after the first, second, ... 429 when no Retry-After came."""

MAX_RETRY_AFTER = 120.0
"""The longest wait a Retry-After header can ask for; a longer one is capped."""


class ChatFailure(Exception):
    """A chat call that did not produce a reply.

    ``message`` is one plain sentence with a next step. It never contains the
    key or a raw response body, so it is safe to show and to log.
    ``retry_after`` is the delay in seconds a 429 asked for, when it gave one.
    """

    def __init__(
        self, reason: Reason, message: str, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.message = message
        self.retry_after = retry_after


def chat(
    provider: Provider,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 128,
    *,
    timeout: float = TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Send one chat request to ``provider`` and return the reply text.

    The endpoint comes only from the Provider table. ``timeout`` is the time
    allowed for each stage of the request, except connecting, which never gets
    more than the default. ``transport`` replaces the network in tests. Raises
    ``ChatFailure`` on any failure.
    """
    spec = PROVIDERS[provider]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    limits = httpx.Timeout(timeout, connect=min(timeout, TIMEOUT_SECONDS))
    try:
        with httpx.Client(transport=transport, timeout=limits) as client:
            response = client.post(
                spec.url,
                content=orjson.dumps(payload),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.TransportError as err:
        # Timeouts and connection errors. The error text is not shown.
        raise ChatFailure(
            "network",
            f"Could not reach {spec.label}; check the internet connection and "
            "try again.",
        ) from err
    if response.status_code != 200:
        raise _failure_for_status(provider, model, response)
    return _reply_text(provider, response)


def _failure_for_status(
    provider: Provider, model: str, response: httpx.Response
) -> ChatFailure:
    label = PROVIDERS[provider].label
    status = response.status_code
    bad_key = status == 400 and "api key" in response.text.lower()
    if status in (401, 403) or bad_key:
        return ChatFailure(
            "auth",
            f"{label} rejected the key; check it in Respec's settings and "
            "save it again.",
        )
    if status == 402:
        return ChatFailure(
            "quota",
            f"{label} says the account is out of credit; check the account's "
            "billing or quota, then try again.",
        )
    if status == 429:
        return ChatFailure(
            "rate_limit",
            f"The {label} rate limit or a quota was reached; try again later.",
            retry_after=_retry_after(response),
        )
    if status == 404:
        return ChatFailure(
            "model_unavailable",
            f"{label} does not offer the model {model}; try again later, or "
            "choose another model once that is possible.",
        )
    return ChatFailure(
        "other",
        f"{label} answered with an unexpected status (HTTP {status}); try again later.",
    )


def _retry_after(response: httpx.Response) -> float | None:
    """The delay in seconds that a ``Retry-After`` header asks for, if it has one.

    Only the seconds form counts; an HTTP date is ignored.
    """
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _reply_text(provider: Provider, response: httpx.Response) -> str:
    finish_reason = reply = None
    try:
        choice = orjson.loads(response.content)["choices"][0]
        finish_reason = choice.get("finish_reason")
        reply = choice["message"]["content"]
    except (orjson.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        pass
    # Checked before the text: a reply cut off in its thinking can be empty.
    if finish_reason == "length":
        raise ChatFailure(
            "output_truncated",
            "The Model's reply was cut off at its output limit; try a shorter "
            "article, or later with another Model.",
        )
    if isinstance(reply, str) and reply.strip():
        return reply.strip()
    raise ChatFailure(
        "other",
        f"{PROVIDERS[provider].label} answered without any reply text; try "
        "again later.",
    )


def chat_with_retries(
    provider: Provider,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    *,
    on_wait: Callable[[int, int, int], None],
    timeout: float = LONG_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Like ``chat``, but retry a 429 up to ``MAX_ATTEMPTS`` times in all.

    The wait honours the 429's ``Retry-After`` (kept between 1 s and
    ``MAX_RETRY_AFTER``) and otherwise follows ``RETRY_DELAYS``. Before each
    wait, ``on_wait(seconds, next_attempt, max_attempts)`` is called. After the
    last attempt the ``rate_limit`` failure is raised; any other failure is
    raised at once. ``sleep`` is injectable so tests do not wait.
    """
    attempt = 1
    while True:
        try:
            return chat(
                provider,
                key,
                model,
                messages,
                max_tokens,
                timeout=timeout,
                transport=transport,
            )
        except ChatFailure as failure:
            if failure.reason != "rate_limit" or attempt == MAX_ATTEMPTS:
                raise
            delay = RETRY_DELAYS[attempt - 1]
            if failure.retry_after is not None:
                delay = min(max(failure.retry_after, 1.0), MAX_RETRY_AFTER)
        on_wait(math.ceil(delay), attempt + 1, MAX_ATTEMPTS)
        sleep(delay)
        attempt += 1
