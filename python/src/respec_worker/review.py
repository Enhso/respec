"""Review pages for checking eval labels against their article.

``review_page`` renders one label file and its article as a self-contained HTML
page: the article with every labelled name highlighted and every labelled
sentence underlined, with the labels beside it. ``write_review`` writes one page
per label file and an index of them. The check itself is in ``evals/README.md``.
"""

import re
from collections.abc import Iterable, Sequence
from html import escape
from pathlib import Path
from typing import Final

import orjson

from respec_worker.evals import Labels, load_labels, read_body
from respec_worker.extraction import DocumentText

KINDS: Final = ("Person", "Organization", "Identity", "Vessel", "Location")
"""The entity kinds highlighted in the article, in legend order."""

_STYLE: Final = """
:root {
  color-scheme: light dark;
  --bg: #fbfbf8; --fg: #1d1d1b; --muted: #66665f; --rule: #dcdcd4;
  --person: #bfdbfe; --organization: #bbf7d0; --identity: #fbcfe8;
  --vessel: #fde68a; --location: #ddd6fe;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161614; --fg: #ebebe6; --muted: #a3a39b; --rule: #3a3a36;
    --person: #1e3a8a; --organization: #14532d; --identity: #831843;
    --vessel: #78350f; --location: #4c1d95;
  }
}
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.55 system-ui, sans-serif; }
main { max-width: 60rem; margin: 0 auto; padding: 16px; overflow-wrap: break-word; }
h1 { font-size: 1.5rem; line-height: 1.2; }
h2 { font-size: 1.15rem; margin-top: 2rem; }
p, li, .article { max-width: 70ch; }
a { color: inherit; }
.meta { color: var(--muted); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid var(--rule); padding: 6px 8px;
  text-align: left; vertical-align: top; }
.article { margin-top: 0.75rem; }
.k-person { background: var(--person); }
.k-organization { background: var(--organization); }
.k-identity { background: var(--identity); }
.k-vessel { background: var(--vessel); }
.k-location { background: var(--location); }
.ul { text-decoration: underline; text-decoration-thickness: 2px;
  text-underline-offset: 3px; }
.legend { list-style: none; padding: 0; }
.legend li { margin: 4px 0; }
.swatch { display: inline-block; width: 1.4em; height: 1em;
  vertical-align: middle; margin-right: 0.4em; border-radius: 3px; }
"""

_LEGEND: Final = (
    '<ul class="legend">'
    + "".join(
        f'<li><span class="swatch k-{kind.lower()}"></span>{kind}</li>'
        for kind in KINDS
    )
    + '<li><span class="swatch ul"></span>Underlined: a sentence the labels quote</li>'
    + "</ul>"
)

_SENTENCE_END: Final = '.!?:\u2026"\u201d\u2019\u00bb)'
"""Characters that may end a sentence; a line break after anything else, or
before a lower-case letter or a comma, is inside a sentence."""

_HOW_TO_CHECK: Final = (
    "Read the article. First note every person, organisation, front, vessel, "
    "place, event or link central to the story that is not highlighted or listed "
    "(missing). Then note anything listed that is wrong or not key."
)


def review_page(labels: Labels, body: str, title: str, url: str) -> str:
    """The review page for ``labels`` against the article ``body``, as one
    complete HTML document.

    The page shows the article first, with every non-Event key Entity's name or
    alias highlighted by kind and every labelled sentence underlined, so the
    check asks what is missing before it reads the lists. The labels follow as
    tables, then the drafter's doubts. Sentences not found in the body are
    not underlined, and nothing is escaped twice.
    """
    names = {entity.id: entity.name for entity in labels.entities}
    kinds, underlined = _mark(labels, body)
    article = _body_html(body, kinds, underlined)
    sections = [
        f"<h1>{escape(title)}</h1>",
        f'<p class="meta"><a href="{escape(url)}">{escape(url)}</a> · '
        f"{len(body)} characters · {_checked_text(labels)}</p>",
        _section("How to check", f"<p>{_HOW_TO_CHECK}</p>"),
        _section("Article", _LEGEND + f'<div class="article">{article}</div>'),
    ]
    entity_rows = [
        (entity.id, entity.kind, entity.name, ", ".join(entity.aliases))
        for entity in labels.key_entities
    ]
    event_rows = [
        (event.id, event.name, event.date or "no date", ", ".join(event.aliases))
        for event in labels.key_events
    ]
    link_rows = [
        (names[link.from_], " or ".join(link.types), names[link.to], link.sentence)
        for link in labels.relationships
    ]
    sections += [
        _section(
            "Key entities",
            _table(["id", "kind", "name", "aliases"], entity_rows),
        ),
        _section(
            "Key events",
            _table(["id", "name", "date", "aliases"], event_rows),
        ),
        _section(
            "Relationships",
            _table(["from", "types", "to", "sentence"], link_rows),
        ),
    ]
    if labels.notes:
        items = "".join(f"<li>{escape(note)}</li>" for note in labels.notes)
        sections.append(_section("Drafter's doubts", f"<ul>{items}</ul>"))
    return _document(title, "\n".join(sections))


def write_review(
    labels_files: list[Path], bodies: Path, documents: Path, out: Path
) -> list[Path]:
    """Write a review page per label file into ``out``, and ``index.html``
    linking them all, and return the written paths with the index last.

    ``bodies`` holds ``<article>.txt`` for each label file. ``documents`` is a
    JSON-lines file with an ``id``, ``title`` and ``url`` for each article.
    """
    records: dict[str, tuple[str, str]] = {}
    for line in documents.read_bytes().splitlines():
        if line.strip():
            record = orjson.loads(line)
            records[record["id"]] = (record["title"], record["url"])
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    rows: list[tuple[str, str, Labels]] = []
    for path in labels_files:
        labels = load_labels(path)
        article = labels.article
        if article not in records:
            raise ValueError(f"no document record for {article} in {documents}")
        title, url = records[article]
        page = out / f"{article}.html"
        page.write_text(
            review_page(labels, read_body(bodies, article), title, url),
            encoding="utf-8",
        )
        written.append(page)
        rows.append((article, title, labels))
    index = out / "index.html"
    index.write_text(_index_page(rows), encoding="utf-8")
    return [*written, index]


def _checked_text(labels: Labels) -> str:
    return "checked" if labels.checked else "not checked yet"


def _name_spans(labels: Labels, body: str) -> list[tuple[int, int, str]]:
    """The (start, end, kind) of every whole-word, case-insensitive match of a
    non-Event key Entity's name or alias in ``body``.

    The longest form is tried first at each position, and matches are taken
    left to right without overlap, so an earlier or longer match wins.
    """
    forms = sorted(
        (
            (form, entity.kind)
            for entity in labels.key_entities
            for form in entity.forms
        ),
        key=lambda pair: -len(pair[0]),
    )
    if not forms:
        return []
    pattern = re.compile(
        "|".join(f"((?<!\\w){re.escape(form)}(?!\\w))" for form, _ in forms),
        re.IGNORECASE,
    )
    return [
        (match.start(), match.end(), forms[(match.lastindex or 0) - 1][1])
        for match in pattern.finditer(body)
    ]


def _mark(labels: Labels, body: str) -> tuple[list[str | None], list[bool]]:
    """The highlight kind (or None) and the underline flag of every character
    of ``body``."""
    kinds: list[str | None] = [None] * len(body)
    for start, end, kind in _name_spans(labels, body):
        for index in range(start, end):
            kinds[index] = kind
    underlined = [False] * len(body)
    document = DocumentText(body)
    sentences = [
        sentence for entity in labels.entities for sentence in entity.sentences
    ]
    sentences += [link.sentence for link in labels.relationships]
    for sentence in sentences:
        found = document.find(sentence)
        if found is not None:
            for index in range(found.start, found.end):
                underlined[index] = True
    return kinds, underlined


def _body_html(body: str, kinds: list[str | None], underlined: list[bool]) -> str:
    """The article as HTML: runs of equal highlight and underline wrapped in one
    span each. A single line break inside a sentence, where the stored text put
    a link on its own line, reads as a space; any other is ``<br>``, and two or
    more newlines are a gap."""
    parts: list[str] = []
    index = 0
    while index < len(body):
        if body[index] in "\r\n":
            end = index
            while end < len(body) and body[end] in "\r\n":
                end += 1
            newlines = body.count("\n", index, end)
            if newlines == 1 and _inside_sentence(body, index, end):
                parts.append(" ")
            elif newlines:
                parts.append("<br>" if newlines == 1 else "<br><br>")
            index = end
            continue
        state = (kinds[index], underlined[index])
        end = index + 1
        while (
            end < len(body)
            and body[end] not in "\r\n"
            and (kinds[end], underlined[end]) == state
        ):
            end += 1
        parts.append(_span(state, body[index:end]))
        index = end
    return "".join(parts)


def _inside_sentence(body: str, start: int, end: int) -> bool:
    """Whether the line break ``body[start:end]`` falls inside a sentence."""
    before = body[start - 1] if start else ""
    after = body[end] if end < len(body) else ""
    if not before or not after:
        return False
    return before not in _SENTENCE_END or after.islower() or after in ",.;:)"


def _span(state: tuple[str | None, bool], text: str) -> str:
    kind, underline = state
    classes: list[str] = []
    if kind is not None:
        classes.append(f"k-{kind.lower()}")
    if underline:
        classes.append("ul")
    if not classes:
        return escape(text)
    return f'<span class="{" ".join(classes)}">{escape(text)}</span>'


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_rows = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    return (
        '<div class="scroll"><table>'
        f"<thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody>"
        "</table></div>"
    )


def _section(heading: str, content: str) -> str:
    return f"<section><h2>{heading}</h2>{content}</section>"


def _document(title: str, content: str) -> str:
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            f"<style>{_STYLE}</style>",
            "</head>",
            "<body>",
            "<main>",
            content,
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _index_page(rows: list[tuple[str, str, Labels]]) -> str:
    items = "".join(
        f'<li><a href="{escape(article)}.html">{escape(title)}</a><br>'
        f'<span class="meta">{len(labels.key_entities)} entities · '
        f"{len(labels.key_events)} events · {len(labels.relationships)} "
        f"relationships · {_checked_text(labels)}</span></li>"
        for article, title, labels in rows
    )
    return _document("Label review", f"<h1>Label review</h1><ul>{items}</ul>")
