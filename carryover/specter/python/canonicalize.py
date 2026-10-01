"""Canonicalise URLs for content-hash dedup keying.

The rules are conservative — they fold the obvious tracking-noise
variants without touching path / query semantics that distinguish
actual articles:

* Scheme + host lowercased; ``www.`` prefix retained (some outlets
  redirect off it, some redirect onto it — both forms should resolve
  to the *fetched* URL, which is the canonical form).
* Fragment dropped.
* Query parameters whose key starts with ``utm_`` removed; query
  parameters in :data:`_TRACKING_PARAMS` removed.
* Path duplicate slashes collapsed.
* Trailing slash removed when the path has more than one segment
  (a bare ``/`` is preserved).
"""

import re
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "fbclid",
        "gclid",
        "ref",
        "ref_src",
        "mc_eid",
        "mc_cid",
        "yclid",
        "_hsenc",
        "_hsmi",
    }
)

# Two-or-more consecutive forward slashes anywhere in the path. The bs4
# stripper at the end of the function preserves a leading ``/`` because
# the regex is applied after :func:`urlparse` has already split scheme
# and netloc off; the path always starts with at most one slash.
_DUPLICATE_SLASH_RE: Final[re.Pattern[str]] = re.compile(r"/{2,}")


def _is_tracking_key(key: str) -> bool:
    """Return ``True`` when ``key`` should be stripped from the URL query."""
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def canonicalize_url(raw: str) -> str:
    """Return the canonical form of ``raw`` for content-hash dedup.

    Args:
        raw: A URL string as provided by the caller (HTTP fetch result,
            feed entry link, manual paste).

    Returns:
        The canonical form. Idempotent:
        ``canonicalize_url(canonicalize_url(x)) == canonicalize_url(x)``.

    Raises:
        ValueError: When ``raw`` is empty, lacks a scheme, or lacks a
            netloc — the caller's URL is structurally unusable.
    """
    if not raw or not raw.strip():
        raise ValueError("canonicalize_url received an empty URL.")

    parsed = urlparse(raw.strip())
    if not parsed.scheme:
        raise ValueError(f"canonicalize_url: URL lacks a scheme: {raw!r}.")
    if not parsed.netloc:
        raise ValueError(f"canonicalize_url: URL lacks a netloc: {raw!r}.")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    path = _DUPLICATE_SLASH_RE.sub("/", parsed.path) if parsed.path else ""
    # Preserve bare ``/`` (or empty path) untouched; strip trailing slash
    # only when at least one path segment is present.
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # ``parse_qsl(..., keep_blank_values=True)`` retains parameters whose
    # value is empty (rare but real — e.g. ``?print=``); preserving them
    # avoids a false-positive dedup against the same article missing the
    # parameter entirely.
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_key(key)
    ]
    query = urlencode(query_pairs, doseq=True)

    # Fragment intentionally dropped: ``#`` is a client-side anchor and
    # has no bearing on the body the extractor will see.
    return urlunparse((scheme, netloc, path, parsed.params, query, ""))
