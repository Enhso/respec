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
        key_env="OPENROUTER_API_KEY",
        # Free OpenRouter Models come and go and are often rate-limited
        # upstream. Chosen by live calls on 2026-10-02: this one answered
        # three times in a row, while google/gemma-4-31b-it:free always
        # returned 429 and qwen/qwen3.8-27b:free did on two of three.
        default_model="nvidia/nemotron-3-super-120b-a12b:free",
    ),
    "gemini": ProviderSpec(
        label="Google Gemini",
        url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        key_env="GEMINI_API_KEY",
        # Flash-Lite answers in about a second where the full Flash Models took
        # 24 s or timed out on 2026-10-02. The alias follows the current one,
        # because pinned Models such as gemini-2.5-flash-lite are closed to new
        # accounts.
        default_model="gemini-flash-lite-latest",
    ),
}
