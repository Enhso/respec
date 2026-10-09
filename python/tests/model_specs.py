"""Fake Provider replies to a Model-spec lookup, shared by the tests."""

import httpx

from respec_worker.providers import Provider


def spec_response(
    provider: Provider, model: str, context_window: int, max_output: int
) -> httpx.Response:
    """The Provider's answer when asked about ``model``: OpenRouter lists a
    neighbour as well, as its real list does; Gemini describes the one Model."""
    if provider == "openrouter":
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "other/neighbour:free",
                        "context_length": 8_000,
                        "top_provider": {"max_completion_tokens": 1_000},
                    },
                    {
                        "id": model,
                        "context_length": context_window,
                        "top_provider": {
                            "context_length": context_window,
                            "max_completion_tokens": max_output,
                        },
                    },
                ]
            },
        )
    return httpx.Response(
        200,
        json={
            "name": f"models/{model}",
            "inputTokenLimit": context_window,
            "outputTokenLimit": max_output,
        },
    )
