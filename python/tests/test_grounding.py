"""Grounding: each supporting sentence is checked against the Document text, and
a found one is emitted as the Document text's own slice with its offsets.

Tests with `verbatim` in the name are ISC-44 (a fabricated sentence is dropped
and counted; quote and whitespace variants of a real one match). Tests with
`offsets` are ISC-44.1 (the offsets slice the Document text back to the sentence).
"""

import logging

import orjson
import pytest

from respec_worker.extraction import (
    DocumentText,
    Pass1EntityProposal,
    ground,
    parse_pass1,
)


def _proposals(*sentence_lists: list[str]) -> list[Pass1EntityProposal]:
    """One valid entity Proposal per list of supporting sentences."""
    items = [
        {"label": "Person", "name": f"Person {n}", "supporting_sentences": sentences}
        for n, sentences in enumerate(sentence_lists, start=1)
    ]
    return parse_pass1(orjson.dumps({"entities": items}).decode()).entities


def _grounded(text: str, *sentences: str) -> list[str]:
    """The text of each sentence of one Proposal that is found in ``text``."""
    result = ground(_proposals(list(sentences)), DocumentText(text), "entity")
    return [s.text for g in result.kept for s in g.sentences]


# ---- ISC-44: verbatim ----

ORDER = "Ada Verrin signed the order in March."


def test_verbatim_a_fabricated_sentence_is_dropped_and_counted() -> None:
    """A sentence that is nowhere in the Document text is dropped, and with it
    the Proposal it was the only support of."""
    result = ground(
        _proposals(["Ada Verrin fled the country."]), DocumentText(ORDER), "entity"
    )

    assert result.kept == []
    assert result.dropped == 1
    assert result.sentences_dropped == 1


def test_verbatim_a_real_sentence_is_kept_and_nothing_is_counted() -> None:
    """Every sentence found: nothing dropped."""
    result = ground(_proposals([ORDER], [ORDER]), DocumentText(ORDER), "entity")

    assert len(result.kept) == 2
    assert (result.dropped, result.sentences_dropped) == (0, 0)


def test_verbatim_a_proposal_stays_on_its_found_sentence_if_the_other_is_fake() -> None:
    """Only the fabricated sentence goes; the Proposal stays on the real one,
    even when the real one was the Model's second."""
    result = ground(
        _proposals(["Ada Verrin fled the country.", ORDER]),
        DocumentText(ORDER),
        "entity",
    )

    (kept,) = result.kept
    assert [s.text for s in kept.sentences] == [ORDER]
    assert (result.dropped, result.sentences_dropped) == (0, 1)


def test_verbatim_a_proposal_with_no_sentence_found_is_dropped_whole() -> None:
    """Both sentences fabricated: two sentences and one Proposal dropped, and the
    Proposal beside it is untouched."""
    result = ground(
        _proposals(["One lie.", "Another lie."], [ORDER]), DocumentText(ORDER), "entity"
    )

    assert [g.proposal.name for g in result.kept] == ["Person 2"]
    assert (result.dropped, result.sentences_dropped) == (1, 2)


@pytest.mark.parametrize(
    ("text", "sentence"),
    [
        ("He said “stop” twice.", 'He said "stop" twice.'),
        ('He said "stop" twice.', "He said “stop” twice."),
        ("It’s Ada’s order.", "It's Ada's order."),
        ("It's Ada's order.", "It’s Ada’s order."),
        ("A ‘small’ order.", "A 'small' order."),
        ("A 'small' order.", "A ‘small’ order."),
        (
            "Mixed “a” and ‘b’ and \"c\" and 'd'.",
            "Mixed \"a\" and 'b' and “c” and ‘d’.",
        ),
    ],
)
def test_verbatim_curly_and_straight_quotes_match_each_way(
    text: str, sentence: str
) -> None:
    """Single and double quotes match whichever way they curl."""
    assert _grounded(text, sentence) == [text]


@pytest.mark.parametrize(
    ("text", "sentence"),
    [
        ("Ada  Verrin\nsigned\tthe   order.", "Ada Verrin signed the order."),
        ("Ada Verrin signed the order.", "Ada  Verrin\nsigned\tthe   order."),
        ("Ada Verrin\r\nsigned the order.", "Ada Verrin signed the order."),
        ("Ada Verrin\u00a0signed the order.", "Ada Verrin signed the order."),
        ("Ada Verrin signed the order.", "  Ada Verrin signed the order.\n"),
        (
            "Intro.\n\n  Ada Verrin signed the order.\n\nEnd.",
            "Ada Verrin signed the order.",
        ),
    ],
)
def test_verbatim_whitespace_runs_match_as_one_space(text: str, sentence: str) -> None:
    """Any run of whitespace equals one space, and the Model's leading and
    trailing whitespace does not count."""
    (found,) = _grounded(text, sentence)

    assert found.strip() == found
    assert " ".join(found.split()) == "Ada Verrin signed the order."


def test_verbatim_the_emitted_sentence_is_the_document_texts_own_not_the_models() -> (
    None
):
    """The Model wrote straight quotes and single spaces; the emitted sentence
    has the Document text's curly quotes, double spaces and line break."""
    text = "Intro. She said “no”,  and\nleft. Outro."

    (found,) = _grounded(text, 'She said "no", and left.')

    assert found == "She said “no”,  and\nleft."


@pytest.mark.parametrize(
    "sentence",
    [
        "ada verrin signed the order in march.",
        "Ada Verrin signed the order in March...",
        "Ada Verrin signed the order—in March.",
        "Ada Verrin signed order in March.",
        "Ada Verrin signed the order in March and left.",
        "Ada Verrin put her name to the order in March.",
        "AdaVerrin signed the order in March.",
        "",
        "   \n\t",
    ],
    ids=[
        "case",
        "ellipsis",
        "dash",
        "missing-word",
        "extra-words",
        "paraphrase",
        "removed-space",
        "empty",
        "blank",
    ],
)
def test_verbatim_only_quotes_and_whitespace_may_differ(sentence: str) -> None:
    """Case, punctuation, words and spacing inside a word all still count."""
    assert _grounded(ORDER, sentence) == []


def test_verbatim_drops_are_logged_by_kind_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One error line per dropped sentence and per dropped Proposal, with no
    sentence, name or Document text in it."""
    proposals = _proposals(["Ada Verrin fled the country."], [ORDER, "Mr Garble lied."])

    with caplog.at_level(logging.ERROR):
        ground(proposals, DocumentText(ORDER), "entity")

    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(lines) == 3
    assert all("entity" in line for line in lines)
    assert sum("sentence: not found" in line for line in lines) == 2
    assert sum("none of its supporting sentences" in line for line in lines) == 1
    text = " ".join(lines)
    for content in ("Verrin", "Garble", "fled", "March", "Person"):
        assert content not in text


# ---- ISC-44.1: offsets ----

CYRILLIC = (
    "Расследование: компания «Долмен Фрейт» и порт Карск\r\n\r\n"
    "Директор Ада Верин сказала: “Мы ничего не знаем”, и 😀 ушла из   зала.\r\n"
    "Груз прибыл в Карск 12 февраля 2024 года.  Компания   «Долмен Фрейт» "
    "отправила его раньше."
)


def test_offsets_slice_the_document_text_back_to_the_emitted_sentence() -> None:
    """On text with Cyrillic names, curly quotes, an emoji, CRLF line ends and
    uneven spacing, `text[start:end]` is exactly the emitted sentence."""
    document = DocumentText(CYRILLIC)
    found = [
        document.find(
            'Директор Ада Верин сказала: "Мы ничего не знаем", и 😀 ушла из зала.'
        ),
        document.find("Груз прибыл в Карск 12 февраля 2024 года."),
        document.find("Компания «Долмен Фрейт» отправила его раньше."),
        document.find("Расследование: компания «Долмен Фрейт» и порт Карск"),
    ]

    assert all(s is not None for s in found)
    sentences = [s for s in found if s is not None]
    for s in sentences:
        assert CYRILLIC[s.start : s.end] == s.text
    assert sentences[0].text == (
        "Директор Ада Верин сказала: “Мы ничего не знаем”, и 😀 ушла из   зала."
    )
    assert sentences[2].text == "Компания   «Долмен Фрейт» отправила его раньше."
    assert sentences[3].text == "Расследование: компания «Долмен Фрейт» и порт Карск"
    assert [s.start for s in sentences] == [CYRILLIC.index(s.text) for s in sentences]


def test_offsets_count_code_points_not_utf16_units_or_bytes() -> None:
    """Two emoji before the sentence move it by two, not by four UTF-16 units or
    eight UTF-8 bytes."""
    text = "😀😀 Ада Верин подписала приказ."

    found = DocumentText(text).find("Ада Верин подписала приказ.")

    assert found is not None
    assert (found.start, found.end) == (3, len(text))
    assert len(text.encode("utf-16-le")) // 2 > len(text)
    assert len(text.encode("utf-8")) > len(text)


def test_offsets_end_is_exclusive_and_stops_before_trailing_whitespace() -> None:
    """The end is the index just past the last character, not past the spaces
    or line break that follow it."""
    text = "First sentence.   \n\nAda Verrin signed the order.  \r\nLast one."

    found = DocumentText(text).find("Ada Verrin signed the order.")

    assert found is not None
    assert text[found.end] == " "
    assert text[found.start : found.end] == "Ada Verrin signed the order."
    assert found.start == text.index("Ada")


def test_offsets_start_after_the_whitespace_that_precedes_the_sentence() -> None:
    """A Model sentence that begins mid-run of whitespace still starts on its
    first character."""
    text = "\n\n   Ada Verrin signed the order.\n"

    found = DocumentText(text).find("  Ada Verrin signed the order.  ")

    assert found is not None
    assert found.start == 5
    assert text[found.start : found.end] == "Ada Verrin signed the order."


def test_offsets_are_those_of_the_first_occurrence() -> None:
    """A sentence found twice is located at the first."""
    text = "Ada Verrin signed. Later, Ada Verrin signed. Ada Verrin signed."

    found = DocumentText(text).find("Ada Verrin signed.")

    assert found is not None
    assert (found.start, found.end) == (0, len("Ada Verrin signed."))


def test_offsets_are_given_for_every_sentence_of_a_proposal() -> None:
    """Both of a Proposal's sentences carry their own offsets, in the Model's order."""
    text = "Alpha one.\nBeta   two.\nGamma three."

    result = ground(
        _proposals(["Gamma three.", "Beta two."]), DocumentText(text), "entity"
    )

    (kept,) = result.kept
    assert [(s.text, text[s.start : s.end]) for s in kept.sentences] == [
        ("Gamma three.", "Gamma three."),
        ("Beta   two.", "Beta   two."),
    ]
    assert [s.start for s in kept.sentences] == [
        text.index("Gamma"),
        text.index("Beta"),
    ]
