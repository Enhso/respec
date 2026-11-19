"""The fixed Provider table: where each Provider's key may be sent.

This is the only place an API endpoint is written down. The HTTP client takes
a Provider, never a URL, so a key cannot be sent to a host chosen at run time
(ISC-37).
"""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

Provider = Literal["openrouter", "gemini"]
"""A Provider, spelled as in ``src/keys.rs`` and the contract fixtures."""


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the worker knows about one Provider."""

    label: str
    """The name shown to the operator."""
    url: str
    """The OpenAI-compatible chat completions endpoint."""
    models_url: str
    """Where the Provider states a Model's limits, on the same host as ``url``.
    ``{model}`` stands for the Model's URL-quoted name, where the endpoint
    describes one Model; OpenRouter's lists them all."""
    key_env: str
    """The environment variable holding the Provider's key."""
    default_model: str
    """The Model used for the test call; choosing a Model is S11."""

    @property
    def host(self) -> str:
        """The host part of ``url``."""
        host = urlsplit(self.url).hostname
        assert host is not None  # the table's URLs are literals with a host
        return host


PROVIDERS: dict[Provider, ProviderSpec] = {
    "openrouter": ProviderSpec(
        label="OpenRouter",
        url="https://openrouter.ai/api/v1/chat/completions",
        models_url="https://openrouter.ai/api/v1/models",
        key_env="OPENROUTER_API_KEY",
        # Free OpenRouter Models come and go and are often rate-limited
        # upstream. Changed on 2026-11-18: the earlier default,
        # nvidia/nemotron-3-super-120b-a12b:free, now answers 404, and this one
        # passed a real-sized extraction on a 28k-character article that day.
        default_model="nvidia/nemotron-3.5-super-vl:free",
    ),
    "gemini": ProviderSpec(
        label="Google Gemini",
        url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        models_url="https://generativelanguage.googleapis.com/v1beta/models/{model}",
        key_env="GEMINI_API_KEY",
        # Flash-Lite answers in about a second where the full Flash Models took
        # 24 s or timed out on 2026-10-02. The alias follows the current one,
        # because pinned Models such as gemini-2.5-flash-lite are closed to new
        # accounts.
        default_model="gemini-flash-lite-latest",
    ),
}

# The Models ``extract --fallback`` walks after the chosen one, in this order,
# until Respec's settings let the Operator choose (S11). Super-VL leads because
# it passed gate A where Flash-Lite did not (2026-11-19: key Event and
# Relationship recall 0.91 and 0.69 against 0.56 and 0.47); Flash-Lite answers
# in about 20 s against 5 to 6 minutes and follows it. Successive entries
# alternate Model families, because the Gemini Flash Models failed together on
# 2026-10-09 (HTTP 503 on every call for over an hour). A Provider without a
# key is skipped. Both nemotron entries completed both passes on a
# 28k-character article on 2026-11-18. gemma-4-31b-it stays out until a clean
# check (a 503, then a bad_output during a worker mutation test, then 914 s),
# so no third family is confirmed and the two nemotron entries share one.
FALLBACK_MODELS: tuple[tuple[Provider, str], ...] = (
    ("openrouter", "nvidia/nemotron-3.5-super-vl:free"),
    ("gemini", "gemini-flash-lite-latest"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
)
