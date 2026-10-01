"""LiteLLM wrapper for the three-pass extraction pipeline.

Two responsibilities live here:

* :func:`call_pass` — issue a single cached-system + uncached-user call via
  the :mod:`api.llm` seam; return the normalised :class:`~api.llm.LLMResult`
  so the orchestrator can read ``result.cost_usd`` and ``result.text`` without
  touching provider-specific types.
* :func:`_strip_json_envelope` / :func:`_scan_balanced` — defensive helpers
  that tolerate the JSON-envelope variants models occasionally emit (bare JSON,
  ``<json>…</json>``-tagged, markdown-fenced JSON, trailing prose).
  :class:`ExtractionResponseError` is the failure taxonomy the orchestrator
  routes to its rejection-audit path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from api.extraction.prompts import SYSTEM_PROMPT
from api.llm import LLMResult, LLMTarget, llm_complete

_REASON_T = Literal["missing_json", "invalid_json", "schema_violation", "empty_output"]


class ExtractionResponseError(ValueError):
    """Raised when a model response cannot be parsed as a pass schema.

    Carries the raw response text and the pass number so the rejection
    audit row has full context for retrospective prompt debugging.

    Attributes:
        pass_number: The pass that failed (``1``, ``2``, or ``3``).
        raw_response: The model's raw text output, before JSON parsing.
        reason: One of the four failure discriminators.
        result: The normalised :class:`~api.llm.LLMResult` returned by
            the provider, when the failure happened after the call
            succeeded. ``None`` only on the (very rare) defensive paths
            where the result was unavailable. The pipeline reads
            ``result.cost_usd`` and ``result.usage`` to record the
            billed cost of a parse-failed call.
        prompt: The fully-formatted user prompt that produced the
            response. Always populated so the audit row records what
            was sent.
    """

    def __init__(
        self,
        *,
        pass_number: Literal[1, 2, 3],
        raw_response: str,
        reason: _REASON_T,
        result: LLMResult | None = None,
        prompt: str | None = None,
    ) -> None:
        """Build a structured message naming the offending pass + reason."""
        super().__init__(f"Pass {pass_number} response could not be parsed: {reason}.")
        self.pass_number = pass_number
        self.raw_response = raw_response
        self.reason = reason
        self.result = result
        self.prompt = prompt


class ExtractionTransportError(RuntimeError):
    """Raised when the LLM API call itself fails.

    Covers rate-limit, 5xx, timeout, and connection-reset paths.

    Carries the prompt so the audit row records what was attempted, and
    the upstream exception for diagnosis. Provider-side billing is
    *not* certain on transport failure (a 429 likely means no billed
    tokens; a 5xx may or may not be billed), so the pipeline writes
    the audit row with ``cost_usd=0.0`` and ``usage=None``.

    Attributes:
        pass_number: The pass that failed.
        prompt: The fully-formatted user prompt.
        cause: The upstream exception from the provider call.
    """

    def __init__(
        self,
        *,
        pass_number: Literal[1, 2, 3],
        prompt: str,
        cause: BaseException,
    ) -> None:
        """Build a structured message naming pass + cause."""
        super().__init__(
            f"Pass {pass_number} transport error: {type(cause).__name__}: {cause}"
        )
        self.pass_number = pass_number
        self.prompt = prompt
        self.cause = cause


async def call_pass(
    *,
    model: str,
    user_prompt: str,
    max_tokens: int,
    api_key: str | None = None,
    api_base: str | None = None,
    fallback: Sequence[LLMTarget] = (),
) -> LLMResult:
    """Send one cached-system + uncached-user message; return the LLM result.

    Delegates to :func:`api.llm.llm_complete`, which attaches the system
    prompt as a content block with ``cache_control: {"type": "ephemeral"}``
    for prompt-cache efficiency on Anthropic models. The raw response,
    token usage, and cost are all normalised into the returned
    :class:`~api.llm.LLMResult`.

    Args:
        model: LiteLLM model string (e.g. ``"anthropic/claude-sonnet-4-6"``).
        user_prompt: The fully-formatted per-pass user message.
        max_tokens: Output token budget.
        api_key: Provider API key. ``None`` defers to litellm's env-var lookup.
        api_base: Base URL for local or proxy providers (e.g. Ollama endpoint).
        fallback: Ordered free-model fallback chain (epic 54). Empty by
            default — pass-through to :func:`api.llm.llm_complete`.

    Returns:
        Normalised :class:`~api.llm.LLMResult`.
    """
    return await llm_complete(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        api_key=api_key,
        api_base=api_base,
        fallback=fallback,
    )


# Three JSON-envelope shapes we tolerate, in priority order:
#   1. ``<json>...</json>`` explicit envelope.
#   2. ```` ```json ... ``` ```` markdown fence (or unlabelled ```` ``` ```` fence).
#   3. Bare JSON document — first balanced ``{ ... }`` from the head of the text.
_JSON_TAG_OPEN: Final[str] = "<json>"
_JSON_TAG_CLOSE: Final[str] = "</json>"


def _strip_json_envelope(
    raw: str,
    *,
    pass_number: Literal[1, 2, 3],
) -> str:
    """Return the first JSON document found inside ``raw``.

    Tolerated envelopes: a ``<json>...</json>`` tag pair, a markdown
    fence (```` ```json ... ``` ```` or unlabelled ```` ``` ```` ), or a
    bare JSON document at the head of the text. Trailing prose after the
    JSON document is discarded — we return up to the matching brace and
    stop.

    Args:
        raw: The raw text from the model response.
        pass_number: For the error message when no JSON is found.

    Returns:
        The JSON-document substring (still as a string; the caller runs
        ``model_validate_json``).

    Raises:
        ExtractionResponseError: With ``reason="missing_json"`` when no
            JSON document is found, or ``reason="empty_output"`` when
            ``raw`` is empty / whitespace-only.
    """
    if not raw or not raw.strip():
        raise ExtractionResponseError(
            pass_number=pass_number, raw_response=raw, reason="empty_output"
        )

    # 1. Explicit <json>...</json> envelope.
    open_idx = raw.find(_JSON_TAG_OPEN)
    if open_idx != -1:
        close_idx = raw.find(_JSON_TAG_CLOSE, open_idx + len(_JSON_TAG_OPEN))
        if close_idx != -1:
            inner = raw[open_idx + len(_JSON_TAG_OPEN) : close_idx]
            return _scan_balanced(inner, pass_number=pass_number)

    # 2. Markdown fence (with or without a language tag).
    fence_idx = raw.find("```")
    if fence_idx != -1:
        # Skip past the opening fence and any language tag on the same line.
        after_fence = raw[fence_idx + 3 :]
        newline = after_fence.find("\n")
        if newline != -1:
            after_fence = after_fence[newline + 1 :]
        close_fence = after_fence.find("```")
        if close_fence != -1:
            inner = after_fence[:close_fence]
            return _scan_balanced(inner, pass_number=pass_number)

    # 3. Bare JSON.
    return _scan_balanced(raw, pass_number=pass_number)


def _scan_balanced(
    text: str,
    *,
    pass_number: Literal[1, 2, 3],
) -> str:
    """Return the first balanced ``{ ... }`` document in ``text``.

    Brace-counts past string literals (single- and double-quoted) and
    skips escaped quotes so a sentence inside a string field cannot
    confuse the scanner. Arrays at the top level (`[ ... ]`) are also
    supported because the Pass-N schemas wrap their lists inside an
    object, but a future schema change to a top-level array would
    otherwise break parsing.

    Args:
        text: A candidate slice that may contain a JSON document.
        pass_number: For the error message when no JSON is found.

    Returns:
        The substring spanning the first balanced document.

    Raises:
        ExtractionResponseError: With ``reason="missing_json"`` when no
            balanced document is found.
    """
    n = len(text)
    i = 0
    while i < n and text[i] not in "{[":
        i += 1
    if i == n:
        raise ExtractionResponseError(
            pass_number=pass_number, raw_response=text, reason="missing_json"
        )
    opener = text[i]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    string_quote = ""
    j = i
    while j < n:
        ch = text[j]
        if in_string:
            if ch == "\\" and j + 1 < n:
                j += 2
                continue
            if ch == string_quote:
                in_string = False
            j += 1
            continue
        if ch in ('"', "'"):
            in_string = True
            string_quote = ch
            j += 1
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[i : j + 1]
        j += 1
    raise ExtractionResponseError(
        pass_number=pass_number, raw_response=text, reason="missing_json"
    )


__all__ = [
    "ExtractionResponseError",
    "ExtractionTransportError",
    "_strip_json_envelope",
    "call_pass",
]
