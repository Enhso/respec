"""Command-line entry point for the Respec worker."""

import argparse
import os
import time
from collections.abc import Callable, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Final, get_args

import httpx

from respec_worker.budget import ModelSpec, lookup_model_spec, output_budget
from respec_worker.client import ChatFailure, chat, chat_with_retries
from respec_worker.extraction import (
    BadOutput,
    DocumentText,
    Grounded,
    Pass1EntityProposal,
    Pass1Response,
    Pass2RelationshipProposal,
    ground,
    known_relationships,
    parse_pass1,
    parse_pass2,
    pass1_messages,
    pass2_messages,
    split_paragraphs,
    stamp_ids,
)
from respec_worker.fallback import AllModelsFailed, Candidate, ModelChain, chain_for
from respec_worker.fetch import FetchError, fetch_article
from respec_worker.messages import (
    DocumentSummary,
    Done,
    Entities,
    Failed,
    Progress,
    ProposedEntity,
    ProposedRelationship,
    Reason,
    Relationships,
    Stage,
    Started,
    emit,
    failure_what,
)
from respec_worker.providers import PROVIDERS, Provider

PASS_COUNT: Final = 2
"""How many Model calls ``extract`` makes: entities, then relationships."""


def _key_from_environment(provider: Provider) -> str | None:
    """The Provider's key from its environment variable, or None after
    emitting a ``failed`` message that says no key is set."""
    spec = PROVIDERS[provider]
    key = os.environ.get(spec.key_env, "").strip()
    if not key:
        emit(
            Failed(
                provider=provider,
                reason="auth",
                message=f"No {spec.label} key is set; save one in Respec's "
                f"settings (or set {spec.key_env} when running from a terminal).",
            )
        )
        return None
    return key


def _waiting(
    provider: Provider, reason: Reason, seconds: int, attempt: int, attempts: int
) -> tuple[Stage, str]:
    """The stage and plain sentence for a wait before retrying a call that
    failed with ``reason``: a rate limit, or an overloaded Provider."""
    retry = f"retrying in {seconds} s (attempt {attempt} of {attempts})"
    if reason == "overloaded":
        return (
            "waiting_overloaded",
            f"{PROVIDERS[provider].label} is overloaded; {retry}",
        )
    return "waiting_rate_limit", f"Rate limited; {retry}"


def _chat_failed(provider: Provider, failure: ChatFailure) -> Failed:
    """The ``failed`` message for a chat call that failed."""
    return Failed(
        provider=provider,
        reason=failure.reason,
        message=failure.message,
        retry_at=failure.retry_at,
    )


def run_test_call(provider: Provider, *, transport: httpx.BaseTransport | None) -> int:
    """Send one small chat request to ``provider`` and report it as messages.

    The key comes from the Provider's environment variable. Emits ``started``,
    then ``done`` (returning 0) or ``failed`` (returning 1). Keeps no state and
    writes no files.
    """
    spec = PROVIDERS[provider]
    key = _key_from_environment(provider)
    if key is None:
        return 1
    emit(Started(provider=provider, model=spec.default_model))
    try:
        reply = chat(
            provider,
            key,
            spec.default_model,
            [{"role": "user", "content": "Reply with the single word: pong"}],
            transport=transport,
        )
    except ChatFailure as failure:
        emit(_chat_failed(provider, failure))
        return 1
    emit(Done(provider=provider, model=spec.default_model, reply=reply))
    return 0


def run_test_extraction(
    provider: Provider,
    url: str,
    *,
    transport: httpx.BaseTransport | None,
    sleep: Callable[[float], None],
) -> int:
    """Fetch the article at ``url`` and run one Pass 1 call over it.

    Emits ``progress`` messages while it works, then ``entities`` (returning 0)
    or ``failed`` (returning 1). The key comes from the Provider's environment
    variable and is sent only to the Provider's host, never to ``url``'s. Keeps
    no state and writes no files.
    """
    spec = PROVIDERS[provider]
    key = _key_from_environment(provider)
    if key is None:
        return 1

    def progress(stage: Stage, detail: str) -> None:
        emit(Progress(provider=provider, stage=stage, detail=detail))

    progress("fetching", "Fetching the article")
    try:
        article = fetch_article(url, transport=transport)
    except FetchError as failure:
        emit(Failed(provider=provider, reason="fetch", message=failure.message))
        return 1

    def waiting(seconds: int, attempt: int, attempts: int, reason: Reason) -> None:
        progress(*_waiting(provider, reason, seconds, attempt, attempts))

    model_spec = lookup_model_spec(
        provider, key, spec.default_model, transport=transport
    )
    progress(
        "calling_model",
        f"Calling {spec.default_model} on {len(article.body):,} characters",
    )
    messages = pass1_messages(article.body)
    try:
        reply = chat_with_retries(
            provider,
            key,
            spec.default_model,
            messages,
            output_budget(model_spec, messages),
            on_wait=waiting,
            transport=transport,
            sleep=sleep,
        )
        response = parse_pass1(reply)
    except ChatFailure as failure:
        emit(_chat_failed(provider, failure))
        return 1
    except BadOutput as failure:
        emit(Failed(provider=provider, reason="bad_output", message=failure.message))
        return 1
    grounding = ground(response.entities, DocumentText(article.body), "entity")
    emit(
        Entities(
            provider=provider,
            model=spec.default_model,
            document=DocumentSummary(
                url=article.final_url,
                title=article.title or None,
                chars=len(article.body),
            ),
            entities=_proposed_entities(stamp_ids(grounding.kept)),
            dropped=response.dropped + grounding.dropped,
            sentences_dropped=grounding.sentences_dropped,
        )
    )
    return 0


def _proposed_entities(
    entities: Mapping[str, Grounded[Pass1EntityProposal]],
) -> list[ProposedEntity]:
    return [
        ProposedEntity(
            id=entity_id,
            label=grounded.proposal.label,
            name=grounded.proposal.name,
            sentence=grounded.sentences[0].text,
            sentence_start=grounded.sentences[0].start,
            sentence_end=grounded.sentences[0].end,
            date=grounded.proposal.date,
            place=grounded.proposal.place,
        )
        for entity_id, grounded in entities.items()
    ]


def _read_text_file(provider: Provider, path: Path) -> str | None:
    """The Document text in the file at ``path``, or None after emitting a
    ``failed`` message that says the file is unreadable or empty."""
    try:
        # Decoded from bytes so that line ends stay as they are in the file and
        # the offsets of a sentence count the file's own characters.
        text = path.read_bytes().decode("utf-8")
    except (OSError, ValueError):
        emit(
            Failed(
                provider=provider,
                reason="fetch",
                message=f"Could not read {path} as UTF-8 text; check the path "
                "and try again.",
            )
        )
        return None
    if not text.strip():
        emit(
            Failed(
                provider=provider,
                reason="fetch",
                message=f"{path} holds no text; give a file with the article's "
                "text in it.",
            )
        )
        return None
    return text


def run_extract(
    provider: Provider,
    *,
    model: str | None = None,
    pass2_chunk_chars: int = 0,
    fallback: bool = False,
    url: str | None,
    text_file: Path | None,
    transport: httpx.BaseTransport | None,
    sleep: Callable[[float], None],
) -> int:
    """Run both extraction passes over one Document and report them as messages.

    The Document is the article at ``url`` or the text in ``text_file``, and the
    Model is ``model``, by default the Provider's default Model. With
    ``pass2_chunk_chars``, Pass 2 is asked once per part of the text of at most
    that many characters, and the parts' links are merged; Pass 1 always reads
    the whole text. With ``fallback``, a pass whose Model still fails after its
    retries moves on to the next Model of ``FALLBACK_MODELS`` whose Provider has
    a key (see ``respec_worker.fallback``), and ``entities`` and
    ``relationships`` name the Model that made them. Emits ``progress``
    messages while it works, ``entities`` once Pass 1 is read, and then
    ``relationships`` (returning 0) or ``failed`` (returning 1). Each key comes
    from its Provider's environment variable and is sent only to that
    Provider's host. Keeps no state and writes no files.
    """
    spec = PROVIDERS[provider]
    key = _key_from_environment(provider)
    if key is None:
        return 1
    model = model or spec.default_model

    def progress(
        stage: Stage,
        detail: str,
        pass_number: int | None = None,
        at: Provider = provider,
    ) -> None:
        emit(
            Progress(
                provider=at,
                stage=stage,
                detail=detail,
                pass_number=pass_number,
                pass_count=None if pass_number is None else PASS_COUNT,
            )
        )

    if text_file is not None:
        text = _read_text_file(provider, text_file)
        if text is None:
            return 1
        body, location, title = text, None, None
    else:
        assert url is not None  # the command line requires one of the two
        progress("fetching", "Fetching the article")
        try:
            article = fetch_article(url, transport=transport)
        except FetchError as failure:
            emit(Failed(provider=provider, reason="fetch", message=failure.message))
            return 1
        body, location, title = article.body, article.final_url, article.title or None

    chain = ModelChain(chain_for(Candidate(provider, model, key), fallback))
    specs: dict[Candidate, ModelSpec | None] = {}

    def ask(
        candidate: Candidate,
        pass_number: int,
        detail: str,
        messages: list[dict[str, str]],
    ) -> str:
        if candidate not in specs:
            specs[candidate] = lookup_model_spec(
                candidate.provider, candidate.key, candidate.model, transport=transport
            )

        def waiting(seconds: int, attempt: int, attempts: int, reason: Reason) -> None:
            stage, detail = _waiting(
                candidate.provider, reason, seconds, attempt, attempts
            )
            progress(stage, detail, pass_number, candidate.provider)

        progress(
            "calling_model",
            f"Pass {pass_number} of {PASS_COUNT}: {detail}",
            pass_number,
            candidate.provider,
        )
        return chat_with_retries(
            candidate.provider,
            candidate.key,
            candidate.model,
            messages,
            output_budget(specs[candidate], messages),
            on_wait=waiting,
            transport=transport,
            sleep=sleep,
        )

    def falling_back(
        pass_number: int,
    ) -> Callable[[Candidate, Reason, Candidate], None]:
        def notify(failed: Candidate, reason: Reason, following: Candidate) -> None:
            what = failure_what(reason, failed.provider, failed.model)
            progress(
                "falling_back",
                f"{failed.model} failed ({what}); trying {following.model} on "
                f"{PROVIDERS[following.provider].label}",
                pass_number,
                following.provider,
            )

        return notify

    document = DocumentText(body)

    def read_entities(candidate: Candidate) -> Pass1Response:
        reply = ask(
            candidate,
            1,
            f"calling {candidate.model} on {len(body):,} characters",
            pass1_messages(body),
        )
        return parse_pass1(reply)

    def read_relationships(
        candidate: Candidate, entities: Mapping[str, Grounded[Pass1EntityProposal]]
    ) -> tuple[list[Grounded[Pass2RelationshipProposal]], int, int]:
        parts = (
            split_paragraphs(body, pass2_chunk_chars) if pass2_chunk_chars else [body]
        )
        links: list[Grounded[Pass2RelationshipProposal]] = []
        seen: set[tuple[str, str, str]] = set()
        dropped = sentences_dropped = 0
        for number, part in enumerate(parts, start=1):
            detail = f"calling {candidate.model} for relationships"
            if len(parts) > 1:
                detail += f", part {number} of {len(parts)}"
            pass2 = parse_pass2(
                ask(candidate, 2, detail, pass2_messages(part, entities))
            )
            grounded_links = ground(pass2.relationships, document, "relationship")
            kept, unresolved = known_relationships(grounded_links.kept, entities)
            dropped += pass2.dropped + grounded_links.dropped + unresolved
            sentences_dropped += grounded_links.sentences_dropped
            for link in kept:
                proposal = link.proposal
                if (proposal.type, proposal.from_id, proposal.to_id) not in seen:
                    seen.add((proposal.type, proposal.from_id, proposal.to_id))
                    links.append(link)
        return links, dropped, sentences_dropped

    try:
        pass1, first = chain.run(read_entities, falling_back(1))
        grounded_entities = ground(pass1.entities, document, "entity")
        entities = stamp_ids(grounded_entities.kept)
        emit(
            Entities(
                provider=first.provider,
                model=first.model,
                document=DocumentSummary(url=location, title=title, chars=len(body)),
                entities=_proposed_entities(entities),
                dropped=pass1.dropped + grounded_entities.dropped,
                sentences_dropped=grounded_entities.sentences_dropped,
            )
        )
        (links, dropped, sentences_dropped), second = chain.run(
            lambda candidate: read_relationships(candidate, entities),
            falling_back(2),
        )
    except AllModelsFailed as failure:
        emit(
            Failed(
                provider=failure.provider,
                reason=failure.reason,
                message=failure.message,
                retry_at=failure.retry_at,
            )
        )
        return 1
    emit(
        Relationships(
            provider=second.provider,
            model=second.model,
            relationships=[
                ProposedRelationship(
                    type=link.proposal.type,
                    from_id=link.proposal.from_id,
                    to_id=link.proposal.to_id,
                    date_from=link.proposal.date_from,
                    date_to=link.proposal.date_to,
                    date_precision=link.proposal.date_precision,
                    sentence=link.sentences[0].text,
                    sentence_start=link.sentences[0].start,
                    sentence_end=link.sentences[0].end,
                )
                for link in links
            ],
            dropped=dropped,
            sentences_dropped=sentences_dropped,
        )
    )
    return 0


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the worker CLI and return the process exit code.

    With no arguments, print the help text to stdout and return 0. The
    ``--version`` flag prints the installed package version and exits. The
    ``test-call`` command makes one live chat call, ``test-extraction`` runs
    Pass 1 over an article, and ``extract`` runs both passes over an article or
    a text file. ``transport`` replaces the network and ``sleep`` replaces the
    rate-limit waits, in tests.
    """
    parser = argparse.ArgumentParser(
        prog="respec-worker",
        description="Respec extraction worker.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"respec-worker {version('respec-worker')}",
    )
    commands = parser.add_subparsers(dest="command")
    call = commands.add_parser(
        "test-call", help="send one small chat request to a Provider"
    )
    call.add_argument("--provider", required=True, choices=get_args(Provider))
    extraction = commands.add_parser(
        "test-extraction", help="run one Pass 1 extraction over an article URL"
    )
    extraction.add_argument("--provider", required=True, choices=get_args(Provider))
    extraction.add_argument("--url", required=True)
    extract = commands.add_parser(
        "extract", help="run both extraction passes over an article URL or a text file"
    )
    extract.add_argument("--provider", required=True, choices=get_args(Provider))
    source = extract.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--text-file", type=Path)
    extract.add_argument(
        "--model", help="the Model to call (default: the Provider's default Model)"
    )
    extract.add_argument(
        "--fallback",
        action="store_true",
        help="when a pass's Model still fails after its retries, move on to the "
        "next Model of the fallback list whose Provider has a key (default: stay "
        "on the Model chosen)",
    )
    extract.add_argument(
        "--pass2-chunk-chars",
        type=int,
        default=0,
        help="ask Pass 2 once per part of at most this many characters (default: "
        "the whole text at once)",
    )
    args = parser.parse_args(argv)
    if args.command == "test-call":
        return run_test_call(args.provider, transport=transport)
    if args.command == "test-extraction":
        return run_test_extraction(
            args.provider, args.url, transport=transport, sleep=sleep
        )
    if args.command == "extract":
        return run_extract(
            args.provider,
            model=args.model,
            pass2_chunk_chars=args.pass2_chunk_chars,
            fallback=args.fallback,
            url=args.url,
            text_file=args.text_file,
            transport=transport,
            sleep=sleep,
        )
    parser.print_help()
    return 0
