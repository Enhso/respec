"""Respec's own small client for the Providers' OpenAI-compatible chat API.

One synchronous function sends one chat request. It returns the reply text or
raises a ``ChatFailure`` carrying a reason the
operator can act on. There are no retries here; the worker adds them later.
"""

import httpx
import orjson

from respec_worker.messages import Reason
from respec_worker.providers import PROVIDERS, Provider

TIMEOUT_SECONDS = 60.0


class ChatFailure(Exception):
    """A chat call that did not produce a reply.

    ``message`` is one plain sentence with a next step. It never contains the
    key or a raw response body, so it is safe to show and to log.
    """

    def __init__(self, reason: Reason, message: str) -> None:
        super().__init__(message)
        self.reason: Reason = reason
        self.message = message


def chat(
    provider: Provider,
    key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 128,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Send one chat request to ``provider`` and return the reply text.

    The endpoint comes only from the Provider table. ``transport`` replaces the
    network in tests. Raises ``ChatFailure`` on any failure.
    """
    spec = PROVIDERS[provider]
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
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


def _reply_text(provider: Provider, response: httpx.Response) -> str:
    try:
        reply = orjson.loads(response.content)["choices"][0]["message"]["content"]
    except (orjson.JSONDecodeError, KeyError, IndexError, TypeError):
        reply = None
    if isinstance(reply, str) and reply.strip():
        return reply.strip()
    raise ChatFailure(
        "other",
        f"{PROVIDERS[provider].label} answered without any reply text; try "
        "again later.",
    )
