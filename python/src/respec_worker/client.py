"""Respec's own small client for the Providers' OpenAI-compatible chat API.

``chat`` sends one chat request and returns the reply text, or raises a
``ChatFailure`` carrying a reason the operator can act on. It does not retry.
``chat_with_retries`` adds the per-minute rate-limit retries a long call needs.
"""

import math
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import orjson

from respec_worker.messages import Reason, describe_reset
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
) -> str:
    """Send one chat request to ``provider`` and return the reply text.

    The endpoint comes only from the Provider table. ``timeout`` is the time
    allowed for each stage of the request, except connecting, which never gets
    more than the default. ``transport`` replaces the network and ``now`` the
    clock, in tests. Raises ``ChatFailure`` on any failure.
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
        raise _failure_for_status(provider, model, response, now())
    return _reply_text(provider, response)


def _failure_for_status(
    provider: Provider, model: str, response: httpx.Response, now: datetime
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
        daily, retry_at = _daily_quota(provider, response, now)
        if daily:
            return ChatFailure(
                "daily_quota",
                f"{label} has used up its free daily quota for {model}; try "
                f"again {describe_reset(retry_at, now)}.",
                retry_at=retry_at,
            )
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
