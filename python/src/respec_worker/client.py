"""Respec's own small client for the Providers' OpenAI-compatible chat API.

``chat`` sends one chat request and returns the reply text, or raises a
``ChatFailure`` carrying a reason the operator can act on. It does not retry.
``chat_with_retries`` adds the per-minute rate-limit retries a long call needs.
"""

import logging
import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
import orjson

from respec_worker.messages import Reason, failure_message
from respec_worker.providers import PROVIDERS, Provider

_LOGGER = logging.getLogger(__name__)

TIMEOUT_SECONDS = 60.0
"""The default timeout: enough for a one-word reply."""

LONG_TIMEOUT_SECONDS = 600.0
"""The read timeout, and the deadline for the whole call, of a real-sized pass;
the 2026-10-01 probe took 213 s."""

MAX_ATTEMPTS = 6
"""How many times ``chat_with_retries`` tries before giving up on a 429."""

RETRY_DELAYS = (5.0, 10.0, 20.0, 40.0, 60.0)
"""Seconds to wait after the first, second, ... 429 when no Retry-After came."""

MAX_RETRY_AFTER = 120.0
"""The longest wait a Retry-After header can ask for; a longer one is capped."""

RETRIED: Final[frozenset[Reason]] = frozenset({"rate_limit", "overloaded"})
"""The failures ``chat_with_retries`` waits out and tries again."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ChatFailure(Exception):
    """A chat call that did not produce a reply.

    ``message`` is one plain sentence with a next step. It never contains the
    key or a raw response body, so it is safe to show and to log.
    ``retry_after`` is the delay in seconds a 429 asked for, when it gave one.
    ``retry_at`` is the UTC time a ``daily_quota`` failure says the quota comes
    back, when the Provider said.
    """

    def __init__(
        self,
        reason: Reason,
        message: str,
        retry_after: float | None = None,
        retry_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.message = message
        self.retry_after = retry_after
        self.retry_at = retry_at


def chat(
    provider: Provider,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 128,
    *,
    timeout: float = TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
    now: Callable[[], datetime] = _utc_now,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Send one chat request to ``provider`` and return the reply text.

    The endpoint comes only from the Provider table. ``timeout`` limits each
    stage of the request, except connecting, which never gets more than the
    default, and also the whole call: a reply still arriving ``timeout``
    seconds after the call began fails as ``network``, however steadily its
    bytes trickle in. ``transport`` replaces the network, and ``now`` and
    ``clock`` the wall clock and the stopwatch, in tests. Raises ``ChatFailure``
    on any failure.
    """
    spec = PROVIDERS[provider]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    limits = httpx.Timeout(timeout, connect=min(timeout, TIMEOUT_SECONDS))
    started = clock()
    try:
        with (
            httpx.Client(transport=transport, timeout=limits) as client,
            client.stream(
                "POST",
                spec.url,
                content=orjson.dumps(payload),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            ) as streamed,
        ):
            body = bytearray()
            for chunk in streamed.iter_bytes():
                body += chunk
                if clock() - started > timeout:
                    raise ChatFailure(
                        "network", failure_message("network", provider, model)
                    )
            # Only these parts of the reply are used below; a copied
            # Content-Encoding would make httpx decode the body twice.
            response = httpx.Response(
                streamed.status_code,
                headers={
                    k: v for k, v in streamed.headers.items() if k == "retry-after"
                },
                content=bytes(body),
            )
    except httpx.TransportError as err:
        # Timeouts and connection errors. The error text is not shown.
        raise ChatFailure(
            "network", failure_message("network", provider, model)
        ) from err
    if response.status_code != 200:
        raise _failure_for_status(provider, model, response, now())
    return _reply_text(provider, model, response)


def _failure_for_status(
    provider: Provider, model: str, response: httpx.Response, now: datetime
) -> ChatFailure:
    status = response.status_code
    retry_at = None
    reason: Reason = "other"
    if status in (401, 403) or (status == 400 and "api key" in response.text.lower()):
        reason = "model_unavailable" if _is_model_gate(provider, response) else "auth"
    elif status == 402:
        reason = "quota"
    elif status == 429:
        daily, retry_at = _daily_quota(provider, response, now)
        reason = "daily_quota" if daily else "rate_limit"
    elif status == 503:
        reason = "overloaded"
    elif status == 404:
        reason = "model_unavailable"
    else:
        _LOGGER.error("%s answered HTTP %d.", PROVIDERS[provider].label, status)
    return ChatFailure(
        reason,
        failure_message(reason, provider, model, retry_at=retry_at, now=now),
        retry_after=_retry_after(response) if reason in RETRIED else None,
        retry_at=retry_at,
    )


def _is_model_gate(provider: Provider, response: httpx.Response) -> bool:
    """Whether a 403 is OpenRouter refusing one Model, not the key.

    OpenRouter gates some free Models to certain apps and says so in
    ``error.message``. Only a readable message that does not mention a key
    counts, so a bad key still reads as ``auth``.
    """
    message = _error_object(response).get("message")
    return (
        provider == "openrouter"
        and response.status_code == 403
        and isinstance(message, str)
        and bool(message)
        and "key" not in message.lower()
    )


def _error_object(response: httpx.Response) -> dict[str, Any]:
    """The ``error`` object of a Provider's JSON error body, or an empty one.

    Gemini's OpenAI-compatible endpoint wraps the error in a one-item array.
    """
    try:
        body = orjson.loads(response.content)
    except orjson.JSONDecodeError:
        return {}
    if isinstance(body, list) and body:
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _daily_quota(
    provider: Provider, response: httpx.Response, now: datetime
) -> tuple[bool, datetime | None]:
    """Whether a 429 is a per-day quota, and when the Provider says it ends.

    Only the Provider's own error detail counts; a body that cannot be read is a
    per-minute rate limit, which is retried.
    """
    error = _error_object(response)
    if provider == "gemini":
        return _gemini_daily_quota(error, now)
    return _openrouter_daily_quota(error)


def _gemini_daily_quota(
    error: dict[str, Any], now: datetime
) -> tuple[bool, datetime | None]:
    """Gemini names the quota in ``details``: a ``QuotaFailure`` violation whose
    ``quotaId`` holds ``PerDay``, and a ``RetryInfo`` ``retryDelay`` like
    ``82606s``."""
    details = error.get("details")
    daily, delay = False, None
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", ""))
        violations = detail.get("violations")
        if kind.endswith("QuotaFailure") and isinstance(violations, list):
            daily = daily or any(
                isinstance(v, dict) and "PerDay" in str(v.get("quotaId", ""))
                for v in violations
            )
        elif kind.endswith("RetryInfo"):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
            delay = float(match[1]) if match else None
    if not daily or delay is None:
        return daily, None
    retry_at = now + timedelta(seconds=delay)
    if retry_at.microsecond:  # whole seconds, rounded up, so it is never early
        retry_at = retry_at.replace(microsecond=0) + timedelta(seconds=1)
    return True, retry_at


def _openrouter_daily_quota(error: dict[str, Any]) -> tuple[bool, datetime | None]:
    """OpenRouter's free-Model daily limit says ``free-models-per-day`` in the
    message and gives the reset in ``metadata.headers`` as epoch milliseconds."""
    if "free-models-per-day" not in str(error.get("message", "")):
        return False, None
    metadata = error.get("metadata")
    headers = metadata.get("headers") if isinstance(metadata, dict) else None
    reset = next(
        (
            value
            for name, value in (headers if isinstance(headers, dict) else {}).items()
            if str(name).lower() == "x-ratelimit-reset"
        ),
        None,
    )
    try:
        return True, datetime.fromtimestamp(float(str(reset)) / 1000, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return True, None


def _retry_after(response: httpx.Response) -> float | None:
    """The delay in seconds that a ``Retry-After`` header asks for, if it has one.

    Only the seconds form counts; an HTTP date is ignored.
    """
    try:
        seconds = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _reply_text(provider: Provider, model: str, response: httpx.Response) -> str:
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
            "output_truncated", failure_message("output_truncated", provider, model)
        )
    if isinstance(reply, str) and reply.strip():
        return reply.strip()
    raise ChatFailure("other", failure_message("other", provider, model))


def chat_with_retries(
    provider: Provider,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    *,
    on_wait: Callable[[int, int, int, Reason], None],
    timeout: float = LONG_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Like ``chat``, but retry a per-minute 429 or a 503 up to ``MAX_ATTEMPTS``
    times in all.

    The wait honours the ``Retry-After`` (kept between 1 s and
    ``MAX_RETRY_AFTER``) and otherwise follows ``RETRY_DELAYS``. Before each
    wait, ``on_wait(seconds, next_attempt, max_attempts, reason)`` is called,
    with ``rate_limit`` or ``overloaded`` as the reason. After the last attempt
    that failure is raised; any other failure, a ``daily_quota`` included, is
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
            if failure.reason not in RETRIED or attempt == MAX_ATTEMPTS:
                raise
            reason = failure.reason
            delay = RETRY_DELAYS[attempt - 1]
            if failure.retry_after is not None:
                delay = min(max(failure.retry_after, 1.0), MAX_RETRY_AFTER)
        on_wait(math.ceil(delay), attempt + 1, MAX_ATTEMPTS, reason)
        sleep(delay)
        attempt += 1
