"""ISC-13: each call's output budget comes from the Model's spec.

``lookup_model_spec`` reads the spec from the Provider's own API; ``output_budget``
turns it and a prompt into ``max_tokens``.
"""

import logging
import math
from collections.abc import Callable
from typing import Any, get_args

import httpx
import pytest
from model_specs import spec_response

from respec_worker.budget import (
    FALLBACK_MAX_TOKENS,
    ModelSpec,
    lookup_model_spec,
    output_budget,
)
from respec_worker.providers import PROVIDERS, Provider

KEY = "secret-key-0123456789"
BODY_MARKER = "RAW-BODY-MARKER"
MODEL = "vendor/some-model:free"


def _messages(*lengths: int) -> list[dict[str, str]]:
    return [{"role": "user", "content": "x" * length} for length in lengths]


# ---- output_budget ----


@pytest.mark.parametrize(
    ("context_window", "max_output", "lengths", "expected"),
    [
        pytest.param(100_000, 8_000, (1_000,), 8_000, id="max-output-binds"),
        pytest.param(10_000, 8_000, (6_000,), 7_000, id="context-binds"),
        pytest.param(10_000, 8_000, (4_001,), 7_999, id="odd-count-rounds-up"),
        pytest.param(10_000, 8_000, (4_000,), 8_000, id="tie"),
        pytest.param(10_000, 8_000, (2_000, 4_000), 7_000, id="sums-all-messages"),
        pytest.param(10_000, 8_000, (), 8_000, id="no-messages"),
    ],
)
def test_output_budget_is_the_smaller_of_max_output_and_context_minus_prompt(
    context_window: int, max_output: int, lengths: tuple[int, ...], expected: int
) -> None:
    """The prompt is estimated as half its characters, rounded up."""
    spec = ModelSpec(context_window=context_window, max_output=max_output)

    budget = output_budget(spec, _messages(*lengths))

    assert budget == expected
    prompt = math.ceil(sum(lengths) / 2)
    assert budget == min(max_output, context_window - prompt)


def test_output_budget_counts_characters_not_bytes() -> None:
    """Cyrillic text is two bytes a character; the estimate is already
    conservative, so it counts characters."""
    spec = ModelSpec(context_window=10_000, max_output=9_000)
    text = "Привет" * 1_000

    budget = output_budget(spec, [{"role": "user", "content": text}])

    assert len(text.encode()) == 12_000
    assert budget == 10_000 - 3_000


def test_output_budget_without_a_spec_is_the_fixed_fallback() -> None:
    """When the lookup failed there is no spec, and the run uses 16,384."""
    assert output_budget(None, _messages(5_000)) == FALLBACK_MAX_TOKENS == 16384


def test_output_budget_is_at_least_one_when_the_prompt_fills_the_context() -> None:
    """A prompt estimated at more than the whole context never gives a zero or
    negative `max_tokens`; the Provider then answers with its own error."""
    spec = ModelSpec(context_window=1_000, max_output=500)

    assert output_budget(spec, _messages(4_000)) == 1


# ---- lookup_model_spec ----


@pytest.mark.parametrize("provider", get_args(Provider))
def test_output_budget_lookup_reads_the_spec_from_the_providers_api(
    provider: Provider,
) -> None:
    """OpenRouter's list gives `context_length` and `top_provider.
    max_completion_tokens` of the entry whose id is the Model; Gemini's
    `models.get` gives `inputTokenLimit` and `outputTokenLimit`."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return spec_response(provider, MODEL, 262_144, 235_929)

    found = lookup_model_spec(
        provider, KEY, MODEL, transport=httpx.MockTransport(handler)
    )

    assert found == ModelSpec(context_window=262_144, max_output=235_929)
    assert len(seen) == 1


def test_output_budget_lookup_gemini_asks_for_one_model_with_a_header_key() -> None:
    """`GET .../v1beta/models/{model}`; the key travels as `x-goog-api-key`,
    which keeps it out of the URL, so it cannot reach a log through it."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return spec_response("gemini", "gemini-flash-lite-latest", 1_048_576, 65_536)

    lookup_model_spec(
        "gemini",
        KEY,
        "gemini-flash-lite-latest",
        transport=httpx.MockTransport(handler),
    )

    (request,) = seen
    assert request.url.path == "/v1beta/models/gemini-flash-lite-latest"
    assert request.url.query == b""
    assert request.headers["x-goog-api-key"] == KEY
    assert "authorization" not in request.headers


def test_output_budget_lookup_openrouter_lists_the_models_with_a_bearer_key() -> None:
    """`GET /api/v1/models`, authorised like the chat call."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return spec_response("openrouter", MODEL, 262_144, 235_929)

    lookup_model_spec("openrouter", KEY, MODEL, transport=httpx.MockTransport(handler))

    (request,) = seen
    assert request.url.path == "/api/v1/models"
    assert request.headers["authorization"] == f"Bearer {KEY}"


def test_output_budget_lookup_quotes_the_model_name_in_the_gemini_path() -> None:
    """A Model name cannot add path segments or a query to the request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    lookup_model_spec(
        "gemini", KEY, "../other?x=1", transport=httpx.MockTransport(handler)
    )

    (request,) = seen
    assert request.url.host == PROVIDERS["gemini"].host
    assert request.url.path.startswith("/v1beta/models/")
    assert request.url.query == b""
    assert request.url.raw_path.count(b"/") == 3


def _answering(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: response


def _raising(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def _openrouter_entry(**top_provider: Any) -> httpx.Response:
    entry = {"id": MODEL, "context_length": 262_144, "top_provider": top_provider}
    return httpx.Response(200, json={"data": [entry]})


FAILURES: list[tuple[Provider, Callable[[httpx.Request], httpx.Response]]] = [
    *[
        (provider, _answering(httpx.Response(status, text=f"{BODY_MARKER} {KEY}")))
        for provider in get_args(Provider)
        for status in (401, 403, 404, 429, 500)
    ],
    *[
        (provider, _answering(httpx.Response(200, text=f"{BODY_MARKER} {KEY}")))
        for provider in get_args(Provider)
    ],
    *[
        (provider, _answering(httpx.Response(200, json=[BODY_MARKER, KEY])))
        for provider in get_args(Provider)
    ],
    *[
        (provider, _raising(error))
        for provider in get_args(Provider)
        for error in (
            httpx.ReadTimeout(f"took too long {KEY}"),
            httpx.ConnectError(f"refused {KEY}"),
        )
    ],
    # OpenRouter: the Model is not in the list, or its entry lacks a limit.
    (
        "openrouter",
        _answering(httpx.Response(200, json={"data": [{"id": "other/model"}]})),
    ),
    ("openrouter", _answering(httpx.Response(200, json={"data": []}))),
    ("openrouter", _answering(_openrouter_entry(max_completion_tokens=None))),
    ("openrouter", _answering(_openrouter_entry())),
    ("openrouter", _answering(_openrouter_entry(max_completion_tokens="big"))),
    ("openrouter", _answering(_openrouter_entry(max_completion_tokens=0))),
    (
        "openrouter",
        _answering(
            httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": MODEL,
                            "top_provider": {"max_completion_tokens": 1_000},
                        }
                    ]
                },
            )
        ),
    ),
    # Gemini: a limit is missing, not a whole number, or not above zero.
    *[
        ("gemini", _answering(httpx.Response(200, json=body)))
        for body in (
            {"inputTokenLimit": 1_048_576},
            {"outputTokenLimit": 65_536},
            {"inputTokenLimit": 1_048_576, "outputTokenLimit": None},
            {"inputTokenLimit": "1048576", "outputTokenLimit": 65_536},
            {"inputTokenLimit": 1_048_576, "outputTokenLimit": 0},
            {"inputTokenLimit": 1_048_576, "outputTokenLimit": True},
            {"inputTokenLimit": 1_048_576.5, "outputTokenLimit": 65_536},
        )
    ],
]


@pytest.mark.parametrize(("provider", "handler"), FAILURES)
def test_output_budget_lookup_failure_is_logged_once_without_key_or_body(
    provider: Provider,
    handler: Callable[[httpx.Request], httpx.Response],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lookup that fails for any reason gives None, so the run goes on with the
    fixed budget, and logs one error that names the Provider and the Model and
    holds neither the key nor any of the response body."""
    with caplog.at_level(logging.ERROR, logger="respec_worker.budget"):
        found = lookup_model_spec(
            provider, KEY, MODEL, transport=httpx.MockTransport(handler)
        )

    assert found is None
    (record,) = [r for r in caplog.records if r.name == "respec_worker.budget"]
    assert record.levelno == logging.ERROR
    text = record.getMessage()
    assert PROVIDERS[provider].label in text and MODEL in text
    assert str(FALLBACK_MAX_TOKENS) in text
    assert KEY not in text and BODY_MARKER not in text
    assert record.exc_info is None
    assert KEY not in caplog.text and BODY_MARKER not in caplog.text
