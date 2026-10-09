"""Tests for the label review pages."""

import json
import re
from pathlib import Path

from respec_worker.evals import Labels
from respec_worker.review import review_page, write_review


def _entity(
    id_: str,
    kind: str,
    name: str,
    sentences: list[str],
    aliases: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": id_,
        "kind": kind,
        "name": name,
        "aliases": aliases or [],
        "sentences": sentences,
    }


def _labels(
    entities: list[dict[str, object]],
    relationships: list[dict[str, object]] | None = None,
    notes: list[str] | None = None,
) -> Labels:
    return Labels.model_validate(
        {
            "article": "a1",
            "checked": False,
            "entities": entities,
            "relationships": relationships or [],
            "notes": notes or [],
        }
    )


def _article(page: str) -> str:
    return page.split("<h2>Article</h2>", 1)[1]


def _classes_of(page: str, text: str) -> set[str]:
    """The classes of every span in the article whose text is ``text``."""
    classes: set[str] = set()
    for span_classes, span_text in _spans(_article(page)):
        if span_text == text:
            classes |= span_classes
    return classes


_SPAN: re.Pattern[str] = re.compile(r'<span class="([^"]*)">([^<]*)</span>')


def _spans(article: str) -> list[tuple[set[str], str]]:
    return [(set(classes.split()), text) for classes, text in _SPAN.findall(article)]


def _underlined(page: str) -> str:
    return "".join(text for classes, text in _spans(_article(page)) if "ul" in classes)


def test_names_and_aliases_are_highlighted_by_kind_ignoring_case() -> None:
    body = "Ada Verrin met Dolmen Freight. ADA VERRIN left. Verrin said nothing.\n"
    labels = _labels(
        [
            _entity(
                "k1",
                "Person",
                "Ada Verrin",
                ["Verrin said nothing."],
                aliases=["Verrin"],
            ),
            _entity(
                "k2",
                "Organization",
                "Dolmen Freight Ltd",
                ["Ada Verrin met Dolmen Freight."],
                aliases=["Dolmen Freight"],
            ),
        ]
    )

    page = review_page(labels, body, "Ada Verrin", "https://example.test/a1")

    assert "k-person" in _classes_of(page, "Ada Verrin")
    assert "k-person" in _classes_of(page, "ADA VERRIN")
    assert "k-person" in _classes_of(page, "Verrin")
    assert "k-organization" in _classes_of(page, "Dolmen Freight")


def test_longer_form_wins_over_a_shorter_one_it_contains() -> None:
    body = "UAB Varnas paid Varnas Brink.\n"
    sentence = "UAB Varnas paid Varnas Brink."
    labels = _labels(
        [
            _entity("k1", "Organization", "UAB Varnas", [sentence], ["Varnas"]),
            _entity("k2", "Person", "Varnas Brink", [sentence]),
        ]
    )

    page = review_page(labels, body, "Varnas", "https://example.test/a1")

    assert "k-organization" in _classes_of(page, "UAB Varnas")
    assert "k-person" in _classes_of(page, "Varnas Brink")
    assert _classes_of(page, "Varnas") == set()


def test_labelled_sentences_are_underlined() -> None:
    body = "Ada Verrin met Dolmen Freight. Nothing else.\n"
    labels = _labels(
        [
            _entity("k1", "Person", "Ada Verrin", ["Ada Verrin met Dolmen Freight."]),
            _entity("k2", "Organization", "Dolmen Freight", ["Dolmen Freight."]),
        ],
        relationships=[
            {
                "from": "k1",
                "to": "k2",
                "types": ["ASSOCIATED_WITH"],
                "sentence": "Nothing else.",
            }
        ],
    )

    page = review_page(labels, body, "Ada", "https://example.test/a1")

    underlined = _underlined(page)
    assert "Ada Verrin met Dolmen Freight." in underlined
    assert "Nothing else." in underlined
    assert "ul" in _classes_of(page, "Ada Verrin")


def test_script_in_a_name_and_ampersand_in_the_body_are_escaped() -> None:
    body = "Ada & Co met Ada Verrin.\n"
    sentence = "Ada & Co met Ada Verrin."
    labels = _labels(
        [
            _entity("k1", "Organization", "Ada & Co", [sentence]),
            _entity("k2", "Organization", "<script>alert(1)</script>", [sentence]),
        ]
    )

    page = review_page(labels, body, "Ada & Co", "https://example.test/a1")

    assert "<script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "k-organization" in _classes_of(page, "Ada &amp; Co")
    assert "Ada & Co" not in page


def test_a_sentence_not_in_the_body_does_not_break_the_page() -> None:
    body = "Ada Verrin met Dolmen Freight.\n"
    labels = _labels(
        [
            _entity("k1", "Person", "Ada Verrin", ["Ada Verrin met Dolmen Freight."]),
            _entity("k2", "Organization", "Dolmen Freight", ["Dolmen Freight."]),
        ],
        relationships=[
            {
                "from": "k1",
                "to": "k2",
                "types": ["ASSOCIATED_WITH"],
                "sentence": "This sentence is not in the article.",
            }
        ],
    )

    page = review_page(labels, body, "Ada", "https://example.test/a1")

    assert "This sentence is not in the article." in page
    assert "k-person" in _classes_of(page, "Ada Verrin")
    assert "k-organization" in _classes_of(page, "Dolmen Freight")


def test_line_breaks_become_br_and_gaps() -> None:
    body = "Line one.\n\nLine two.\nLine three.\n"
    page = review_page(_labels([]), body, "Lines", "https://example.test/a1")

    assert "Line one.<br><br>Line two.<br>" in _article(page)


def test_a_line_break_inside_a_sentence_reads_as_a_space() -> None:
    """The stored text put each link on its own line; the page joins them back
    and keeps a break after a full stop."""
    body = (
        "Two men were detained, according to a\nreport\nby\nTidemark\n. Next one.\nLast"
    )
    page = review_page(_labels([]), body, "Lines", "https://example.test/a1")

    assert "according to a report by Tidemark . Next one.<br>Last" in _article(page)


def test_the_article_comes_before_the_lists() -> None:
    """The check asks what is missing before it reads the drafted lists."""
    page = review_page(_labels([]), "Body.\n", "T", "https://example.test/a1")

    assert page.index("<h2>Article</h2>") < page.index("<h2>Key entities</h2>")


def test_write_review_writes_a_page_per_label_file_and_an_index(
    tmp_path: Path,
) -> None:
    bodies = tmp_path / "bodies"
    bodies.mkdir()
    (bodies / "a1.txt").write_text("Ada Verrin met Dolmen Freight.\n", "utf-8")
    (bodies / "a2.txt").write_text("Port Karsk closed.\n", "utf-8")
    documents = tmp_path / "documents.jsonl"
    documents.write_text(
        json.dumps(
            {"id": "a1", "title": "Ada Verrin in Port Karsk", "url": "https://x/a1"}
        )
        + "\n"
        + json.dumps({"id": "a2", "title": "Port Karsk closes", "url": "https://x/a2"})
        + "\n",
        "utf-8",
    )
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    first = labels_dir / "a1.json"
    first.write_text(
        json.dumps(
            {
                "article": "a1",
                "checked": True,
                "entities": [
                    _entity(
                        "k1", "Person", "Ada Verrin", ["Ada Verrin met Dolmen Freight."]
                    )
                ],
                "relationships": [],
            }
        ),
        "utf-8",
    )
    second = labels_dir / "a2.json"
    second.write_text(
        json.dumps(
            {
                "article": "a2",
                "checked": False,
                "entities": [
                    _entity("k1", "Location", "Port Karsk", ["Port Karsk closed."])
                ],
                "relationships": [],
            }
        ),
        "utf-8",
    )
    out = tmp_path / "review"

    written = write_review([first, second], bodies, documents, out)

    assert written == [out / "a1.html", out / "a2.html", out / "index.html"]
    assert all(path.is_file() for path in written)
    assert "<title>Ada Verrin in Port Karsk</title>" in (out / "a1.html").read_text(
        "utf-8"
    )
    index = (out / "index.html").read_text("utf-8")
    assert 'href="a1.html"' in index
    assert 'href="a2.html"' in index
    assert "Port Karsk closes" in index
    assert "not checked yet" in index
