"""The article fetch: engines, title and date, and how each failure maps."""

from datetime import date
from pathlib import Path

import httpx
import pytest
from bs4 import BeautifulSoup

from respec_worker.fetch import (
    _BODY_FLOOR_CHARS,
    FetchError,
    _extract_published_date,
    _extract_title,
    fetch_article,
)

ARTICLES = Path(__file__).parent / "fixtures" / "articles"
URL = "https://news.example/articles/42"


def _serving(name: str, *, status: int = 200) -> httpx.MockTransport:
    html = (ARTICLES / name).read_text(encoding="utf-8")
    return httpx.MockTransport(lambda request: httpx.Response(status, text=html))


def _failing_with(error: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return httpx.MockTransport(handler)


def test_structural_selector_gives_the_body_title_and_date() -> None:
    """An <article> page is read by BeautifulSoup, without its page chrome."""
    article = fetch_article(URL, transport=_serving("campaign_documents.html"))

    assert article.extraction_engine == "beautifulsoup"
    assert "Halvard Centre" in article.body
    for chrome in ("nav junk", "footer junk", "window.ads"):
        assert chrome not in article.body
    assert article.title == "Inside Halvard's quiet campaign across the Lower Marches"
    assert article.published == date(2024, 8, 12)
    assert article.final_url == URL


def test_a_page_without_og_title_or_date_uses_the_title_element() -> None:
    """The <title> is the fallback title, and a missing date is None."""
    article = fetch_article(URL, transport=_serving("shipment_trace.html"))

    assert article.extraction_engine == "beautifulsoup"
    assert "Port Verrin" in article.body
    assert article.title == "Tracing one shipment — Tidemark"
    assert article.published is None


def test_a_page_matching_no_selector_falls_back_to_trafilatura() -> None:
    """Without a structural block, trafilatura finds the paragraphs."""
    article = fetch_article(URL, transport=_serving("minimal_no_selector.html"))

    assert article.extraction_engine == "trafilatura"
    assert "generic div wrapper" in article.body
    assert len(article.body) >= _BODY_FLOOR_CHARS
    assert article.title == "Short note from Riga"


def test_a_page_with_only_navigation_has_no_body() -> None:
    """Neither engine finds text, so the error says so."""
    with pytest.raises(FetchError) as excinfo:
        fetch_article(URL, transport=_serving("empty_body.html"))

    assert excinfo.value.reason == "body_too_short"
    assert "no readable article text" in excinfo.value.message


def test_an_http_error_status_is_a_fetch_failure_naming_the_status() -> None:
    """A 503 is `fetch_failed`, and the page it sent is not in the message."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, text="RAW-BODY-MARKER")
    )

    with pytest.raises(FetchError) as excinfo:
        fetch_article(URL, transport=transport)

    assert excinfo.value.reason == "fetch_failed"
    assert "HTTP 503" in excinfo.value.message
    assert "RAW-BODY-MARKER" not in excinfo.value.message


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("dns lookup failed"), httpx.ReadTimeout("took too long")],
)
def test_a_connection_error_is_a_fetch_failure_without_the_error_text(
    error: Exception,
) -> None:
    """Transport errors are `fetch_failed`, and their text is not shown."""
    with pytest.raises(FetchError) as excinfo:
        fetch_article(URL, transport=_failing_with(error))

    assert excinfo.value.reason == "fetch_failed"
    assert "dns lookup failed" not in excinfo.value.message
    assert "took too long" not in excinfo.value.message


@pytest.mark.parametrize("url", ["ftp://news.example/a", "not a url", ""])
def test_an_address_that_is_not_http_is_a_fetch_failure(url: str) -> None:
    """A bad address fails cleanly, and nothing is requested."""
    transport = _failing_with(AssertionError("a request was made"))

    with pytest.raises(FetchError) as excinfo:
        fetch_article(url, transport=transport)

    assert excinfo.value.reason == "fetch_failed"


def test_redirects_are_followed_and_the_final_address_is_reported() -> None:
    """The article found after a redirect, and its address, are returned."""
    html = (ARTICLES / "campaign_documents.html").read_text(encoding="utf-8")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/short":
            return httpx.Response(
                302, headers={"Location": "https://news.example/en/politics/12345"}
            )
        return httpx.Response(200, text=html)

    article = fetch_article(
        "https://news.example/short", transport=httpx.MockTransport(handler)
    )

    assert [r.url.path for r in seen] == ["/short", "/en/politics/12345"]
    assert article.final_url == "https://news.example/en/politics/12345"
    assert seen[0].headers["user-agent"].startswith("RespecBot/")


def test_extract_title_prefers_og_title_over_the_title_element() -> None:
    """`og:title` wins, and `<title>` is the fallback."""
    both = BeautifulSoup(
        '<head><meta property="og:title" content="OG wins"><title>loser</title></head>',
        "lxml",
    )
    only_title = BeautifulSoup("<head><title>fallback</title></head>", "lxml")
    neither = BeautifulSoup("<head></head>", "lxml")

    assert _extract_title(both) == "OG wins"
    assert _extract_title(only_title) == "fallback"
    assert _extract_title(neither) == ""


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("2024-06-01T10:00:00Z", date(2024, 6, 1)),
        ("2024-06-01", date(2024, 6, 1)),
        ("last Tuesday", None),
    ],
)
def test_extract_published_date_reads_iso_dates_and_ignores_the_rest(
    content: str, expected: date | None
) -> None:
    """ISO dates parse, including a `Z` suffix; anything else is None."""
    soup = BeautifulSoup(
        f'<head><meta property="article:published_time" content="{content}"></head>',
        "lxml",
    )
    assert _extract_published_date(soup) == expected


def test_extract_published_date_is_none_when_the_page_gives_none() -> None:
    """No meta tag means no date."""
    assert _extract_published_date(BeautifulSoup("<head></head>", "lxml")) is None
