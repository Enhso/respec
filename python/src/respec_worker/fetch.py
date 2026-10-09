"""Fetch an article by URL and extract its Document text, title and date.

Ported from Specter's ``api/ingest/fetch.py`` (commit 17a94aa), made
synchronous like ``client.py``. Two engines run in turn:

1. BeautifulSoup against a chain of structural selectors (``<article>``,
   ``<main>``, ``[role="main"]`` and a few common content classes); the text of
   the first block that matches is the body.
2. If that gives fewer than ``_FALLBACK_THRESHOLD_CHARS`` characters,
   trafilatura's heuristic extractor runs and its text wins if it is longer.
3. If the result is still under ``_BODY_FLOOR_CHARS``, the page has no article
   and ``FetchError`` is raised with reason ``body_too_short``.

The title comes from ``og:title``, then ``<title>``. The date comes from
``article:published_time``, then ``og:published_time``.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Literal

import httpx
import trafilatura
from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

_FALLBACK_THRESHOLD_CHARS: Final = 1024
_BODY_FLOOR_CHARS: Final = 256
_HTTP_TIMEOUT_SECONDS: Final = 30.0
_USER_AGENT: Final = "RespecBot/0.1 (+https://github.com/Enhso/respec)"

_BODY_SELECTORS: Final = (
    "article",
    "main",
    "[role='main']",
    "div.article-body",
    "div.entry-content",
    "div.post-content",
    "div.content",
)

_BLOCK_TAGS: Final = (
    "address",
    "article",
    "aside",
    "blockquote",
    "dd",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
)
"""Elements that start a new line in the Document text; all others are inline
and join the text around them as written."""

_LOGGER = logging.getLogger(__name__)


class FetchError(Exception):
    """An article that could not be fetched, or that has no body.

    ``message`` is one plain sentence with a next step. It holds no raw
    response body and no exception text, so it is safe to show and to log.
    """

    def __init__(
        self, reason: Literal["fetch_failed", "body_too_short"], message: str
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True, slots=True)
class FetchedArticle:
    """What ``fetch_article`` found at a URL."""

    final_url: str
    """The URL after redirects."""
    title: str
    """``og:title``, else ``<title>``, else an empty string."""
    published: date | None
    """The publication date, or None when the page does not give one."""
    body: str
    """The Document text, not truncated."""
    extraction_engine: Literal["beautifulsoup", "trafilatura"]
    """Which engine produced the body."""


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
    """Pull the title with the ``og:title`` then ``<title>`` fallback chain."""
    og = _extract_meta_content(soup, "og:title")
    if og:
        return og
    title_tag = soup.find("title")
    if isinstance(title_tag, Tag) and title_tag.string:
        return title_tag.string.strip()
    return ""


def _extract_published_date(soup: BeautifulSoup) -> date | None:
    """Pull the publish date from ``article:published_time`` or ``og:``."""
    raw = _extract_meta_content(soup, "article:published_time", "og:published_time")
    if raw is None:
        return None
    try:
        # fromisoformat takes both "2024-01-01T00:00:00+00:00" and
        # "2024-01-01"; a "Z" suffix needs normalising first.
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        _LOGGER.warning("unparseable publish date %r, ignoring it", raw)
        return None


def _block_text(root: Tag) -> str:
    """The text of ``root`` with one line per block element or ``<br>``.

    Inline elements such as links and emphasis join the text around them as
    written, so "<a>LLC Varnas Trade</a>, and" stays "LLC Varnas Trade, and". A line
    break in the page's source is only whitespace. Runs of whitespace inside a
    line become one space, and empty lines are dropped.
    """
    for node in root.find_all(string=True):
        if type(node) is NavigableString:  # not a comment or a CDATA section
            node.replace_with(re.sub(r"\s+", " ", node))
    for br in root.find_all("br"):
        br.replace_with("\n")
    for block in root.find_all(_BLOCK_TAGS):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = (" ".join(line.split()) for line in root.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def _extract_body_bs4(html: str) -> str:
    """The text of the first block the selector chain matches, a line per
    paragraph.

    Scripts, styles and page chrome are dropped first, so inline JavaScript
    does not reach the Model. Returns an empty string when nothing matches.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    for selector in _BODY_SELECTORS:
        match = soup.select_one(selector)
        if match is None:
            continue
        text = _block_text(match)
        if text:
            return text
    return ""


def _extract_body_trafilatura(html: str) -> str:
    """Run trafilatura's heuristic extractor, the fallback engine."""
    extracted = trafilatura.extract(
        html,
        favor_recall=True,
        include_comments=False,
        include_tables=False,
        no_fallback=False,
    )
    return extracted or ""


def _parse_article(html: str, final_url: str) -> FetchedArticle:
    """Run both engines and the title and date extraction over ``html``."""
    soup = BeautifulSoup(html, "lxml")
    body = _extract_body_bs4(html)
    engine: Literal["beautifulsoup", "trafilatura"] = "beautifulsoup"
    if len(body) < _FALLBACK_THRESHOLD_CHARS:
        fallback = _extract_body_trafilatura(html)
        if len(fallback) > len(body):
            body = fallback
            engine = "trafilatura"
    if len(body) < _BODY_FLOOR_CHARS:
        raise FetchError(
            "body_too_short",
            "The page has no readable article text; try the article's own "
            "address, or another article.",
        )
    return FetchedArticle(
        final_url=final_url,
        title=_extract_title(soup),
        published=_extract_published_date(soup),
        body=body,
        extraction_engine=engine,
    )


def _web_address(url: str) -> httpx.URL:
    """Parse ``url``, which must be an http or https address with a host."""
    try:
        address = httpx.URL(url)
    except httpx.InvalidURL:
        address = None
    if address is None or address.scheme not in ("http", "https") or not address.host:
        raise FetchError(
            "fetch_failed",
            "That is not a web address; it should start with http:// or https://.",
        )
    return address


def fetch_article(
    url: str, *, transport: httpx.BaseTransport | None = None
) -> FetchedArticle:
    """Fetch ``url`` and extract its body, title and date.

    Redirects are followed. ``transport`` replaces the network in tests.
    Raises ``FetchError`` with reason ``fetch_failed`` when ``url`` is not an
    http(s) address or the page cannot be fetched, and ``body_too_short`` when
    neither engine finds an article.
    """
    address = _web_address(url)
    try:
        with httpx.Client(
            transport=transport,
            timeout=_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            response = client.get(address)
            response.raise_for_status()
            html = response.text
            final_url = str(response.url)
    except httpx.HTTPStatusError as err:
        raise FetchError(
            "fetch_failed",
            f"The site answered HTTP {err.response.status_code} for that "
            "address; check the address, or try another article.",
        ) from err
    except httpx.HTTPError as err:
        raise FetchError(
            "fetch_failed",
            "Could not reach that address; check it and the internet "
            "connection, then try again.",
        ) from err
    return _parse_article(html, final_url)
