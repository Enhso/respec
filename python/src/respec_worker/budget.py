"""The output-token budget of each Model call, from the Model's own spec.

Each Provider states a Model's limits in its own API. ``lookup_model_spec``
reads them once per run, and ``output_budget`` turns them and a prompt into the
``max_tokens`` of one call. If the lookup fails the run goes on with
``FALLBACK_MAX_TOKENS``.
"""

import logging
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import quote

import httpx
import orjson

from respec_worker.client import TIMEOUT_SECONDS
from respec_worker.providers import PROVIDERS, Provider

_LOGGER = logging.getLogger(__name__)

FALLBACK_MAX_TOKENS: Final = 16384
"""The budget when a Model's spec could not be looked up.

Specter's fixed 4,096 truncated a 28k-character article; the 2026-10-01 probe
needed about 8k. Both default Models allow more than this.
"""


@dataclass(frozen=True)
class ModelSpec:
    """The limits a Provider states for one Model, in tokens."""

    context_window: int
    max_output: int


class _Unusable(Exception):
    """The Provider's answer did not give the Model's limits; the text says why
    in words that hold no response body."""


def lookup_model_spec(
    provider: Provider,
    key: str,
    model: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ModelSpec | None:
    """Ask ``provider`` for ``model``'s limits, or return None after logging why.

    The endpoint comes only from the Provider table, and the key goes in a
    header. A failed lookup is logged without the key or the response body; the
    caller then uses ``FALLBACK_MAX_TOKENS``. ``transport`` replaces the network
    in tests.
    """
    spec = PROVIDERS[provider]
    url = spec.models_url.format(model=quote(model, safe=""))
    headers = (
        {"Authorization": f"Bearer {key}"}
        if provider == "openrouter"
        else {"x-goog-api-key": key}
    )
    try:
        try:
            with httpx.Client(transport=transport, timeout=TIMEOUT_SECONDS) as client:
                response = client.get(url, headers=headers)
        except httpx.TransportError as err:
            raise _Unusable("the Provider could not be reached") from err
        if response.status_code != 200:
            raise _Unusable(f"HTTP {response.status_code}")
        return _spec_from(provider, response.content, model)
    except _Unusable as err:
        _LOGGER.error(
            "Could not look up the output limits of %s on %s (%s); using the "
            "fixed budget of %d tokens.",
            model,
            spec.label,
            err,
            FALLBACK_MAX_TOKENS,
        )
        return None


def _spec_from(provider: Provider, content: bytes, model: str) -> ModelSpec:
    """The limits in the Provider's answer ``content`` for ``model``."""
    try:
        payload = orjson.loads(content)
        if provider == "openrouter":
            context_window, max_output = _openrouter_limits(payload, model)
        else:
            context_window = payload["inputTokenLimit"]
            max_output = payload["outputTokenLimit"]
    except (orjson.JSONDecodeError, KeyError, IndexError, TypeError):
        raise _Unusable("the answer was not a Model spec") from None
    if not (_is_limit(context_window) and _is_limit(max_output)):
        raise _Unusable("the answer does not state both limits")
    return ModelSpec(context_window=context_window, max_output=max_output)


def _openrouter_limits(payload: Any, model: str) -> tuple[Any, Any]:
    """The context length and the max completion tokens of ``model``'s entry in
    OpenRouter's list."""
    for entry in payload["data"]:
        if isinstance(entry, dict) and entry.get("id") == model:
            return entry["context_length"], entry["top_provider"][
                "max_completion_tokens"
            ]
    raise _Unusable("the Provider's list does not hold the Model")


def _is_limit(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def output_budget(spec: ModelSpec | None, messages: list[dict[str, str]]) -> int:
    """The ``max_tokens`` for a call that sends ``messages`` to the Model.

    It is the Model's max output, capped at its context window minus the
    prompt. No tokenizer is public for these Models, so the prompt is estimated
    at half its characters, rounded up; that overestimates, and Cyrillic text
    runs dense. Never below 1. Without a ``spec`` it is ``FALLBACK_MAX_TOKENS``.
    """
    if spec is None:
        return FALLBACK_MAX_TOKENS
    characters = sum(len(message["content"]) for message in messages)
    prompt = (characters + 1) // 2
    return max(1, min(spec.max_output, spec.context_window - prompt))
