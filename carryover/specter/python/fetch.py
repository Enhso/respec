"""Async HTML fetch + body / title / date extraction.

Two-engine policy (locked in :file:`docs/epics/14-rss-poller-plan.md`):

1. :class:`bs4.BeautifulSoup` against an opinionated selector chain
   (``<article>``, ``<main>``, ``[role="main"]``, generic fallback);
   join text from the first matching block.
2. If step 1 yields under :data:`_FALLBACK_THRESHOLD_CHARS` characters,
   retry with :func:`trafilatura.extract`.
3. If step 2 still yields under :data:`_BODY_FLOOR_CHARS`, raise
   :class:`IngestError` with ``reason="body_too_short"``.

Title is pulled from ``<meta property="og:title">`` → ``<title>``; date
from ``<meta property="article:published_time">`` →
``<meta property="og:published_time">`` → ``None`` (callers pass a
feed-supplied fallback or the pipeline falls back to "today").

The HTTP fetch is async (:mod:`httpx`); the synchronous bs4 /
trafilatura parsing runs inside :func:`asyncio.to_thread` so the event
loop stays unblocked through what can be a 500 ms parse on a large
article.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime
from typing import Final

import httpx
import trafilatura
from bs4 import BeautifulSoup, Tag

from api.ingest._errors import IngestError

_FALLBACK_THRESHOLD_CHARS: Final[int] = 1024
_BODY_FLOOR_CHARS: Final[int] = 256
_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0
_USER_AGENT: Final[str] = (
    "SpecterBot/0.3 (+https://github.com/Enhso/specter; contact via repo)"
)

_BODY_SELECTORS: Final[tuple[str, ...]] = (
    "article",
    "main",
    "[role='main']",
    "div.article-body",
    "div.entry-content",
    "div.post-content",
    "div.content",
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchedArticle:
    """Wire-shape returned by :func:`fetch_article`.

    Attributes:
        final_url: The URL after redirects (for re-canonicalisation by
            the caller).
        title: Article title; ``meta[og:title]`` → ``<title>`` → empty.
        published: Article publish date;
            ``meta[article:published_time]`` →
            ``meta[og:published_time]`` → ``None`` (callers fall back).
        body: Extracted body text (untruncated; the caller applies
            :func:`api.cost.truncate_body`).
        extraction_engine: ``"beautifulsoup"`` or ``"trafilatura"``.
    """

    final_url: str
    title: str
    published: _date | None
    body: str
    extraction_engine: str


def _extract_meta_content(soup: BeautifulSoup, *property_values: str) -> str | None:
    """Return the first non-empty ``<meta property=X content=...>`` value."""
    for prop in property_values:
        tag = soup.find("meta", attrs={"property": prop})
        if isinstance(tag, Tag):
            content = tag.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


def _extract_title(soup: BeautifulSoup) -> str:
    """Pull article title with the ``og:title`` → ``<title>`` fallback chain."""
    og = _extract_meta_content(soup, "og:title")
    if og:
        return og
    title_tag = soup.find("title")
    if isinstance(title_tag, Tag) and title_tag.string:
        return title_tag.string.strip()
    return ""


def _extract_published_date(soup: BeautifulSoup) -> _date | None:
    """Pull publish date from ``article:published_time`` / ``og:published_time``."""
    raw = _extract_meta_content(soup, "article:published_time", "og:published_time")
    if raw is None:
        return None
    try:
        # ``fromisoformat`` accepts ``2024-01-01T00:00:00+00:00`` and the
        # shorter ``2024-01-01`` shape. Outlets that emit ``Z`` suffix
        # need a tiny normalisation pass.
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        _LOGGER.warning("unparseable publish date %r — falling through", raw)
        return None


def _extract_body_bs4(html: str) -> str:
    """Run BeautifulSoup with the opinionated selector chain.

    Returns the joined text of the first selector that matches. Empty
    string when no selector hits — callers translate that into the
    trafilatura fallback or the ``body_too_short`` error.
    """
    soup = BeautifulSoup(html, "lxml")
    # Strip script/style/noscript before joining text so a heavy
    # JS-rendered page does not flood the body with inline JS that
    # the extractor would later be billed to scan.
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    for selector in _BODY_SELECTORS:
        match = soup.select_one(selector)
        if match is None:
            continue
        text = match.get_text(separator="\n", strip=True)
        if text:
            return text
    return ""


def _extract_body_trafilatura(html: str) -> str:
    """Run trafilatura's heuristic extractor (the fallback engine)."""
    extracted = trafilatura.extract(
        html,
        favor_recall=True,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    return extracted or ""


def _parse_article(html: str, *, url: str) -> tuple[str, str, _date | None, str]:
    """Run the two-engine cascade plus title + date extraction.

    Returns ``(body, title, published, engine)``. Raises
    :class:`IngestError("body_too_short")` when neither engine yields
    enough text to be useful.
    """
    soup = BeautifulSoup(html, "lxml")
    title = _extract_title(soup)
    published = _extract_published_date(soup)

    body = _extract_body_bs4(html)
    engine = "beautifulsoup"
    if len(body) < _FALLBACK_THRESHOLD_CHARS:
        fallback = _extract_body_trafilatura(html)
        if len(fallback) > len(body):
            body = fallback
            engine = "trafilatura"

    if len(body) < _BODY_FLOOR_CHARS:
        raise IngestError(
            reason="body_too_short",
            detail=(
                f"Both bs4 ({len(body)} chars) and trafilatura "
                f"extracted under {_BODY_FLOOR_CHARS} chars from {url!r}."
            ),
            url=url,
        )
    return body, title, published, engine


async def fetch_article(url: str) -> FetchedArticle:
    """Fetch ``url`` and extract title / date / body.

    The HTTP fetch is async; the synchronous bs4 / trafilatura parsing
    runs inside :func:`asyncio.to_thread` so the event loop stays free
    during what can be a 500 ms parse on a large article.

    Args:
        url: An absolute URL. Callers are expected to have run
            :func:`canonicalize_url` first; this function does not
            canonicalise (callers re-canonicalise the redirect-final
            URL after the response returns).

    Returns:
        :class:`FetchedArticle` carrying body, title, optional date,
        the final URL, and which extraction engine produced the body.

    Raises:
        IngestError: With ``reason="fetch_failed"`` on any HTTP-layer
            failure, ``reason="body_too_short"`` when both extraction
            engines starve.
    """
    headers = {"User-Agent": _USER_AGENT}
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
            final_url = str(response.url)
    except httpx.HTTPError as exc:
        raise IngestError(
            reason="fetch_failed",
            detail=f"HTTP fetch of {url!r} failed: {exc!s}",
            url=url,
        ) from exc

    body, title, published, engine = await asyncio.to_thread(
        _parse_article, html, url=final_url
    )
    return FetchedArticle(
        final_url=final_url,
        title=title,
        published=published,
        body=body,
        extraction_engine=engine,
    )
