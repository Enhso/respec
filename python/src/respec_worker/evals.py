"""Gate A: how much of what matters in an article a free Model proposes.

Labels list each eval article's key Entities, Events and Relationships
(ISC-42). ``lint`` checks them, ``run`` caches ``extract`` runs of a Model over
the eval articles, ``score`` prints recall from the cached runs (ISC-43, 43.1
and 62), and ``review`` writes pages for checking the labels. The layout and
the rules are in ``evals/README.md``. Run it from ``python/``.
"""

import argparse
import datetime
import re
import statistics
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Self, get_args

import orjson
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from respec_worker.extraction import (
    DocumentText,
    EntityLabel,
    PartialDate,
    RelationshipType,
)
from respec_worker.messages import (
    MESSAGE_ADAPTER,
    Entities,
    Failed,
    ProposedEntity,
    ProposedRelationship,
    Relationships,
)
from respec_worker.providers import Provider

LONG_ARTICLE_CHARS: Final = 15_000
"""Articles over this many characters count as long (ISC-42, and the chunking
question)."""

SAMPLES: Final = 3
"""Successful runs per article and Model: one draw says little (ISA Learning,
2026-10-03)."""

MAX_FAILURES: Final = 3
"""Failed runs per article and Model, rate limits aside, before ``run`` gives up
on the article."""

RUN_TIMEOUT_SECONDS: Final = 1800.0
"""The longest one ``extract`` run may take, retries and both passes included."""

_REPO: Final = Path("..")
ARTICLES_FILE: Final = _REPO / "evals" / "articles.txt"
LABELS_DIR: Final = _REPO / "evals" / "labels"
RUNS_DIR: Final = _REPO / "evals" / "runs"
BODIES_DIR: Final = _REPO / "corpus" / "bodies"
DOCUMENTS_FILE: Final = _REPO / "corpus" / "documents.jsonl"
REVIEW_DIR: Final = _REPO / "evals" / "review"

_Text = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class KeyEntity(BaseModel):
    """One key Entity of an article, Events included, as labelled."""

    model_config = ConfigDict(extra="forbid")

    id: _Text
    kind: EntityLabel
    name: _Text
    aliases: list[_Text] = Field(default_factory=list)
    date: PartialDate | None = None
    sentences: list[_Text] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _only_an_event_has_a_date(self) -> Self:
        if self.kind != "Event" and self.date is not None:
            raise ValueError("only an Event has a date")
        return self

    @property
    def forms(self) -> list[str]:
        """The name and every alias."""
        return [self.name, *self.aliases]


class KeyRelationship(BaseModel):
    """One key Relationship of an article, with every type a fair reviewer
    would accept for it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: _Text = Field(alias="from")
    to: _Text
    types: list[RelationshipType] = Field(min_length=1)
    sentence: _Text


class Labels(BaseModel):
    """The labels of one eval article."""

    model_config = ConfigDict(extra="forbid")

    article: _Text
    checked: bool
    entities: list[KeyEntity]
    relationships: list[KeyRelationship]
    notes: list[_Text] = Field(default_factory=list)
    """The drafter's doubts, for the check: what might be missing or wrong."""

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        kinds = {entity.id: entity.kind for entity in self.entities}
        if len(kinds) != len(self.entities):
            raise ValueError("entity ids must be unique")
        for n, link in enumerate(self.relationships, start=1):
            if link.from_ not in kinds or link.to not in kinds:
                raise ValueError(f"relationship {n} names an unknown entity id")
            if link.from_ == link.to:
                raise ValueError(f"relationship {n} links an entity to itself")
            participant = "PARTICIPATED_IN" in link.types
            if participant and (
                kinds[link.to] != "Event" or kinds[link.from_] == "Event"
            ):
                raise ValueError(
                    f"relationship {n} is PARTICIPATED_IN but does not go from "
                    "a non-Event to an Event"
                )
        return self

    @property
    def key_entities(self) -> list[KeyEntity]:
        """The key Entities that are not Events."""
        return [entity for entity in self.entities if entity.kind != "Event"]

    @property
    def key_events(self) -> list[KeyEntity]:
        """The key Events."""
        return [entity for entity in self.entities if entity.kind == "Event"]


def name_tokens(name: str) -> tuple[str, ...]:
    """``name`` as lower-case words, with accents, punctuation and a leading
    "the" removed, so that the forms of one name compare equal."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    letters = "".join(
        ch if ch.isalnum() else " "
        for ch in decomposed
        if not unicodedata.combining(ch)
    )
    words = tuple(letters.split())
    return words[1:] if words[:1] == ("the",) and len(words) > 1 else words


def load_labels(path: Path) -> Labels:
    """The labels in the file at ``path``; raises ``ValueError`` (a pydantic
    ``ValidationError`` included) when they do not fit the format."""
    return Labels.model_validate_json(path.read_bytes())


def read_body(bodies: Path, article: str) -> str:
    """The Document text of corpus article ``article``, decoded from bytes so
    that offsets count the file's own characters, as ``extract`` does."""
    return (bodies / f"{article}.txt").read_bytes().decode("utf-8")


def lint(labels: Labels, body: str) -> list[str]:
    """The problems with ``labels`` against the article ``body``, each one line.

    Every labelled sentence must be found in the body under the worker's own
    matching rule, and every key Entity other than an Event must have its name
    or an alias in the body, which catches misspelt names.
    """
    document = DocumentText(body)
    body_words = " " + " ".join(name_tokens(body)) + " "
    problems: list[str] = []
    for entity in labels.entities:
        for sentence in entity.sentences:
            if document.find(sentence) is None:
                problems.append(f"{entity.id}: sentence not found: {sentence[:80]}")
        if entity.kind != "Event" and not any(
            f" {' '.join(name_tokens(form))} " in body_words for form in entity.forms
        ):
            problems.append(f"{entity.id}: no form of its name is in the body")
    for n, link in enumerate(labels.relationships, start=1):
        if document.find(link.sentence) is None:
            problems.append(
                f"relationship {n}: sentence not found: {link.sentence[:80]}"
            )
    return problems


def _lint_command(paths: list[Path], bodies: Path) -> int:
    """Lint each label file and print its problems, then the ISC-42 counts."""
    files = paths or sorted(LABELS_DIR.glob("*.json"))
    failed = checked = long = 0
    for path in files:
        try:
            labels = load_labels(path)
            body = read_body(bodies, labels.article)
        except (OSError, ValueError) as err:
            print(f"{path}: {err}")
            failed += 1
            continue
        problems = lint(labels, body)
        if labels.article != path.stem:
            problems.append(f"article {labels.article} differs from the file name")
        for problem in problems:
            print(f"{path.name}: {problem}")
        failed += bool(problems)
        checked += labels.checked
        long += len(body) > LONG_ARTICLE_CHARS
        print(
            f"{path.name}: {len(labels.key_entities)} entities, "
            f"{len(labels.key_events)} events, {len(labels.relationships)} "
            f"relationships, {len(body):,} chars, "
            f"{'checked' if labels.checked else 'not checked'}"
        )
    print(
        f"{len(files)} label files, {checked} checked, {long} over "
        f"{LONG_ARTICLE_CHARS:,} characters, {failed} with problems"
    )
    return 1 if failed else 0


def model_folder(model: str) -> str:
    """The folder name of ``model``'s runs: its name with anything other than
    letters, digits, dots and dashes made an underscore."""
    return re.sub(r"[^A-Za-z0-9.-]", "_", model)


def read_articles(path: Path) -> list[str]:
    """The corpus ids listed in ``path``, one per line."""
    return [
        line.strip() for line in path.read_text("utf-8").splitlines() if line.strip()
    ]


@dataclass(frozen=True, slots=True)
class Attempt:
    """How one ``extract`` run ended: its stdout, whether it proposed
    Relationships, how long it took, and if it failed, the reason and the
    worker's plain message (which never holds article text or a key)."""

    stdout: bytes
    ok: bool
    reason: str | None
    seconds: float
    message: str | None = None


def _last_message(stdout: bytes) -> tuple[str | None, Failed | None]:
    """The kind of the last message in ``stdout``, and that message if it is
    a failure."""
    kind: str | None = None
    failed: Failed | None = None
    for line in stdout.splitlines():
        try:
            message = MESSAGE_ADAPTER.validate_json(line)
        except ValidationError:
            continue
        kind = message.kind
        failed = message if isinstance(message, Failed) else None
    return kind, failed


def extract_once(
    worker: Path, provider: Provider, model: str, body: Path, timeout: float
) -> Attempt:
    """Run ``worker extract`` once over the text file ``body`` and say how it
    ended. The key reaches the worker through the inherited environment."""
    started = time.monotonic()
    command = [str(worker), "extract", "--provider", provider, "--model", model]
    try:
        done = subprocess.run(
            [*command, "--text-file", str(body)],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        stdout = err.stdout if isinstance(err.stdout, bytes) else b""
        return Attempt(stdout, False, "timeout", time.monotonic() - started)
    seconds = time.monotonic() - started
    kind, failed = _last_message(done.stdout)
    if done.returncode == 0 and kind == "relationships":
        return Attempt(done.stdout, True, None, seconds)
    if failed is not None:
        return Attempt(done.stdout, False, failed.reason, seconds, failed.message)
    return Attempt(done.stdout, False, f"exit {done.returncode}", seconds)


_RATE_LIMITED: Final = ("rate_limit", "quota")


def _log_lines(folder: Path) -> list[dict[str, object]]:
    log = folder / "log.jsonl"
    if not log.exists():
        return []
    return [orjson.loads(line) for line in log.read_bytes().splitlines() if line]


def run_model(
    provider: Provider,
    model: str,
    articles: Sequence[str],
    *,
    bodies: Path,
    runs: Path,
    extract: Callable[[Path], Attempt],
    samples: int = SAMPLES,
    max_failures: int = MAX_FAILURES,
    say: Callable[[str], None] = print,
) -> int:
    """Fill ``runs/<model>/<article>/`` with ``samples`` successful runs per
    article, resuming from what is already there.

    Each success is saved as ``s<n>.jsonl``, the worker's stdout, and every
    attempt is appended to ``log.jsonl``. An article is given up for this run
    after ``max_failures`` failures that were not rate limits; a later run
    tries it again, since a Provider's outage passes. Two rate-limited
    failures in a row stop the whole run, since the Provider's quota is spent
    for now; it returns 1 then, and 0 otherwise. ``extract`` runs the worker
    once over a body file.
    """
    limited = 0
    for article in articles:
        folder = runs / model_folder(model) / article
        folder.mkdir(parents=True, exist_ok=True)
        done = len(list(folder.glob("s*.jsonl")))
        failures = 0
        while done < samples and failures < max_failures:
            attempt = extract(bodies / f"{article}.txt")
            if attempt.ok:
                done += 1
                (folder / f"s{done}.jsonl").write_bytes(attempt.stdout)
                limited = 0
            elif attempt.reason in _RATE_LIMITED:
                limited += 1
            else:
                failures += 1
                limited = 0
            line = {
                "ok": attempt.ok,
                "reason": attempt.reason,
                "message": attempt.message,
                "seconds": round(attempt.seconds, 1),
                "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            }
            with (folder / "log.jsonl").open("ab") as log:
                log.write(orjson.dumps(line) + b"\n")
            outcome = "ok" if attempt.ok else attempt.reason
            say(
                f"{model} {article[:8]}: {outcome} in {attempt.seconds:.0f} s "
                f"({done}/{samples} samples)"
            )
            if limited >= 2:
                say(f"{model}: rate limited twice in a row; run again later to resume")
                return 1
        if done < samples:
            say(f"{model} {article[:8]}: gave up after {failures} failures")
    return 0


@dataclass(frozen=True, slots=True)
class Proposals:
    """What one successful run proposed."""

    entities: list[ProposedEntity]
    relationships: list[ProposedRelationship]


def read_run(path: Path) -> Proposals:
    """The Proposals in the saved run at ``path``."""
    entities: list[ProposedEntity] = []
    relationships: list[ProposedRelationship] = []
    for line in path.read_bytes().splitlines():
        message = MESSAGE_ADAPTER.validate_json(line)
        if isinstance(message, Entities):
            entities = message.entities
        elif isinstance(message, Relationships):
            relationships = message.relationships
    return Proposals(entities, relationships)


def _contains(words: tuple[str, ...], part: tuple[str, ...]) -> bool:
    """Whether ``part`` occurs in ``words`` as a run of whole words."""
    size = len(part)
    return size > 0 and any(
        words[i : i + size] == part for i in range(len(words) - size + 1)
    )


def _kinds_fit(proposed: str, key: str) -> bool:
    """Whether a proposed kind may stand for a key kind when only part of the
    name matches: the same kind, or an Identity for a Person or an
    Organization."""
    pair = {proposed, key}
    return proposed == key or pair in (
        {"Identity", "Person"},
        {"Identity", "Organization"},
    )


@dataclass(frozen=True, slots=True)
class _Span:
    start: int
    end: int


def event_spans(labels: Labels, document: DocumentText) -> dict[str, list[_Span]]:
    """Where each key Event's sentences are in the Document text."""
    spans: dict[str, list[_Span]] = {}
    for event in labels.key_events:
        found = (document.find(sentence) for sentence in event.sentences)
        spans[event.id] = [_Span(s.start, s.end) for s in found if s is not None]
    return spans


def recalled_keys(
    entity: ProposedEntity, labels: Labels, spans: dict[str, list[_Span]]
) -> set[str]:
    """The ids of the key Entities and Events that ``entity`` recalls, under
    the rules in ``evals/README.md``. ``spans`` are the key Events' sentences
    in the Document text, from ``event_spans``."""
    words = name_tokens(entity.name)
    if not words:
        return set()
    named: set[str] = set()
    placed: set[str] = set()
    for key in labels.entities:
        forms = [name_tokens(form) for form in key.forms]
        if words in forms or (
            _kinds_fit(entity.label, key.kind)
            and any(_contains(words, form) for form in forms)
        ):
            named.add(key.id)
        elif (
            key.kind == "Event"
            and entity.label == "Event"
            and any(
                span.start < entity.sentence_end and entity.sentence_start < span.end
                for span in spans.get(key.id, [])
            )
        ):
            placed.add(key.id)
    # A sentence can report two Events; an Event named as one key Event is
    # not also credited with another whose sentence it shares.
    events = {key.id for key in labels.key_events}
    return named if named & events else named | placed


@dataclass(frozen=True, slots=True)
class Tally:
    """How many labelled items were recalled, out of how many."""

    hits: int = 0
    total: int = 0

    def __add__(self, other: "Tally") -> "Tally":
        return Tally(self.hits + other.hits, self.total + other.total)

    @property
    def recall(self) -> float | None:
        return self.hits / self.total if self.total else None


@dataclass(frozen=True, slots=True)
class RunScore:
    """Recall of one run against one article's labels."""

    entities: Tally
    events: Tally
    relationships: Tally
    relationships_any_type: Tally

    def __add__(self, other: "RunScore") -> "RunScore":
        return RunScore(
            self.entities + other.entities,
            self.events + other.events,
            self.relationships + other.relationships,
            self.relationships_any_type + other.relationships_any_type,
        )


EMPTY_SCORE: Final = RunScore(Tally(), Tally(), Tally(), Tally())


def score_run(labels: Labels, document: DocumentText, run: Proposals) -> RunScore:
    """How much of ``labels`` the Proposals of ``run`` recall."""
    spans = event_spans(labels, document)
    recalled = {e.id: recalled_keys(e, labels, spans) for e in run.entities}
    found = set().union(*recalled.values()) if recalled else set()

    def tally(keys: Iterable[KeyEntity]) -> Tally:
        ids = [key.id for key in keys]
        return Tally(sum(key in found for key in ids), len(ids))

    typed = any_type = 0
    for key in labels.relationships:
        matches = [
            link
            for link in run.relationships
            if _joins(key, recalled.get(link.from_id), recalled.get(link.to_id))
        ]
        any_type += bool(matches)
        typed += any(link.type in key.types for link in matches)
    total = len(labels.relationships)
    return RunScore(
        tally(labels.key_entities),
        tally(labels.key_events),
        Tally(typed, total),
        Tally(any_type, total),
    )


def _joins(
    key: KeyRelationship, source: set[str] | None, target: set[str] | None
) -> bool:
    """Whether a proposed link whose ends recall ``source`` and ``target``
    joins the key Relationship's two ends, in either direction."""
    if source is None or target is None:
        return False
    return (key.from_ in source and key.to in target) or (
        key.from_ in target and key.to in source
    )


@dataclass(frozen=True, slots=True)
class ArticleResult:
    """One article's runs for one Model: the summed score, the samples it came
    from, the proposal counts per sample, and the attempts' log."""

    article: str
    chars: int
    checked: bool
    score: RunScore
    samples: int
    entity_counts: list[int]
    relationship_counts: list[int]
    failures: Counter[str]
    seconds: list[float]


def score_article(
    labels: Labels, body: str, folder: Path, samples: int = SAMPLES
) -> ArticleResult:
    """Score the first ``samples`` saved runs in ``folder`` against
    ``labels``."""
    document = DocumentText(body)
    runs = [read_run(path) for path in sorted(folder.glob("s*.jsonl"))[:samples]]
    total = EMPTY_SCORE
    for run in runs:
        total += score_run(labels, document, run)
    log = _log_lines(folder)
    return ArticleResult(
        article=labels.article,
        chars=len(body),
        checked=labels.checked,
        score=total,
        samples=len(runs),
        entity_counts=[len(run.entities) for run in runs],
        relationship_counts=[len(run.relationships) for run in runs],
        failures=Counter(str(line["reason"]) for line in log if not line["ok"]),
        seconds=[float(str(line["seconds"])) for line in log if line["ok"]],
    )


GATES: Final = {"entities": 0.8, "events": 0.6, "relationships": 0.6}
"""The gate A thresholds: ISC-43, ISC-62 and ISC-43.1."""


def _ratio(tally: Tally) -> str:
    recall = tally.recall
    return "  -  " if recall is None else f"{recall:.2f}"


def report(
    model: str, results: Sequence[ArticleResult], say: Callable[[str], None]
) -> None:
    """Print one Model's recall: overall against the gates, for long and short
    articles, and per article."""
    total = sum((r.score for r in results), EMPTY_SCORE)
    long = sum((r.score for r in results if r.chars > LONG_ARTICLE_CHARS), EMPTY_SCORE)
    short = sum(
        (r.score for r in results if r.chars <= LONG_ARTICLE_CHARS), EMPTY_SCORE
    )
    samples = sum(r.samples for r in results)
    failures = sum((r.failures for r in results), Counter[str]())
    seconds = [s for r in results for s in r.seconds]
    checked = sum(r.checked for r in results)
    say(
        f"\n{model}: {samples} samples over {len(results)} articles "
        f"({checked} with checked labels); failed attempts: "
        f"{dict(failures) or 'none'}; median run "
        f"{statistics.median(seconds) if seconds else 0:.0f} s"
    )
    rows = (
        ("entities", "ISC-43", total.entities, long.entities, short.entities),
        ("events", "ISC-62", total.events, long.events, short.events),
        (
            "relationships",
            "ISC-43.1",
            total.relationships,
            long.relationships,
            short.relationships,
        ),
    )
    for name, claim, overall, long_part, short_part in rows:
        gate = GATES[name]
        recall = overall.recall
        verdict = "-" if recall is None else ("pass" if recall >= gate else "FAIL")
        say(
            f"  {name:<14}{_ratio(overall)}  ({overall.hits}/{overall.total})  "
            f"{claim} >= {gate}: {verdict}   long {_ratio(long_part)}  "
            f"short {_ratio(short_part)}"
        )
    say(
        f"  relationships with any type: {_ratio(total.relationships_any_type)}  "
        f"({total.relationships_any_type.hits}/{total.relationships_any_type.total})"
    )
    say("  article    chars  samples  entities  events  rels  any-type  proposed e/r")
    for r in results:
        proposed = (
            f"{statistics.mean(r.entity_counts):.0f}/"
            f"{statistics.mean(r.relationship_counts):.0f}"
            if r.samples
            else "-"
        )
        say(
            f"  {r.article[:8]}  {r.chars:>6,}  {r.samples:>7}  "
            f"{_ratio(r.score.entities):>8}  {_ratio(r.score.events):>6}  "
            f"{_ratio(r.score.relationships):>4}  "
            f"{_ratio(r.score.relationships_any_type):>8}  {proposed:>12}"
        )


def _score_command(
    runs: Path, labels_dir: Path, bodies: Path, models: list[str]
) -> int:
    """Score every Model with runs (or only ``models``) on every labelled
    article, and print the reports."""
    folders = sorted(p for p in runs.iterdir() if p.is_dir()) if runs.exists() else []
    if models:
        wanted = {model_folder(m) for m in models}
        folders = [f for f in folders if f.name in wanted]
    label_files = sorted(labels_dir.glob("*.json"))
    if not folders or not label_files:
        print("Nothing to score: no runs or no label files.")
        return 1
    labelled = [load_labels(path) for path in label_files]
    texts = {labels.article: read_body(bodies, labels.article) for labels in labelled}
    for folder in folders:
        results = [
            score_article(labels, texts[labels.article], folder / labels.article)
            for labels in labelled
            if (folder / labels.article).is_dir()
        ]
        report(folder.name, results, print)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the evals command line and return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m respec_worker.evals")
    commands = parser.add_subparsers(dest="command", required=True)
    lint_parser = commands.add_parser("lint", help="check label files")
    lint_parser.add_argument("paths", nargs="*", type=Path)
    lint_parser.add_argument("--bodies", type=Path, default=BODIES_DIR)
    run_parser = commands.add_parser(
        "run", help="cache extract runs of one Model over the eval articles"
    )
    run_parser.add_argument("--provider", required=True, choices=get_args(Provider))
    run_parser.add_argument("--model", required=True)
    run_parser.add_argument("--samples", type=int, default=SAMPLES)
    run_parser.add_argument("--articles", type=Path, default=ARTICLES_FILE)
    score_parser = commands.add_parser("score", help="print recall from cached runs")
    score_parser.add_argument("models", nargs="*")
    for sub in (run_parser, score_parser):
        sub.add_argument("--bodies", type=Path, default=BODIES_DIR)
        sub.add_argument("--runs", type=Path, default=RUNS_DIR)
    score_parser.add_argument("--labels", type=Path, default=LABELS_DIR)
    review_parser = commands.add_parser(
        "review", help="write pages for checking the label files"
    )
    review_parser.add_argument("paths", nargs="*", type=Path)
    review_parser.add_argument("--bodies", type=Path, default=BODIES_DIR)
    review_parser.add_argument("--out", type=Path, default=REVIEW_DIR)
    args = parser.parse_args(argv)
    if args.command == "lint":
        return _lint_command(args.paths, args.bodies)
    if args.command == "run":
        worker = Path(sys.executable).with_name("respec-worker")

        def extract(body: Path) -> Attempt:
            return extract_once(
                worker, args.provider, args.model, body, RUN_TIMEOUT_SECONDS
            )

        return run_model(
            args.provider,
            args.model,
            read_articles(args.articles),
            bodies=args.bodies,
            runs=args.runs,
            extract=extract,
            samples=args.samples,
            say=lambda line: print(line, flush=True),
        )
    if args.command == "score":
        return _score_command(args.runs, args.labels, args.bodies, args.models)
    if args.command == "review":
        from respec_worker.review import write_review  # it imports this module

        paths = args.paths or sorted(LABELS_DIR.glob("*.json"))
        written = write_review(paths, args.bodies, DOCUMENTS_FILE, args.out)
        print(f"Open {written[-1].resolve()}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
