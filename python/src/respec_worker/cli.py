"""Command-line entry point for the Respec worker."""

import argparse
import os
import time
from collections.abc import Callable, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Final, get_args

import httpx

from respec_worker.client import ChatFailure, chat, chat_with_retries
from respec_worker.extraction import (
    PASS1_MAX_TOKENS,
    BadOutput,
    Pass1EntityProposal,
    known_relationships,
    parse_pass1,
    parse_pass2,
    pass1_messages,
    pass2_messages,
    stamp_ids,
)
from respec_worker.fetch import FetchError, fetch_article
from respec_worker.messages import (
    DocumentSummary,
    Done,
    Entities,
    Failed,
    Progress,
    ProposedEntity,
    ProposedRelationship,
    Relationships,
    Stage,
    Started,
    emit,
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
        emit(Failed(provider=provider, reason=failure.reason, message=failure.message))
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

    def waiting(seconds: int, attempt: int, attempts: int) -> None:
        progress(
            "waiting_rate_limit",
            f"Rate limited; retrying in {seconds} s (attempt {attempt} of {attempts})",
        )

    progress(
        "calling_model",
        f"Calling {spec.default_model} on {len(article.body):,} characters",
    )
    try:
        reply = chat_with_retries(
            provider,
            key,
            spec.default_model,
            pass1_messages(article.body),
            PASS1_MAX_TOKENS,
            on_wait=waiting,
            transport=transport,
            sleep=sleep,
        )
        response = parse_pass1(reply)
    except ChatFailure as failure:
        emit(Failed(provider=provider, reason=failure.reason, message=failure.message))
        return 1
    except BadOutput as failure:
        emit(Failed(provider=provider, reason="bad_output", message=failure.message))
        return 1
    emit(
        Entities(
            provider=provider,
            model=spec.default_model,
            document=DocumentSummary(
                url=article.final_url,
                title=article.title or None,
                chars=len(article.body),
            ),
            entities=_proposed_entities(stamp_ids(response)),
            dropped=response.dropped,
        )
    )
    return 0


def _proposed_entities(
    entities: Mapping[str, Pass1EntityProposal],
) -> list[ProposedEntity]:
    return [
        ProposedEntity(
            id=entity_id,
            label=entity.label,
            name=entity.name,
            sentence=entity.supporting_sentences[0],
            date=entity.date,
            place=entity.place,
        )
        for entity_id, entity in entities.items()
    ]


def _read_text_file(provider: Provider, path: Path) -> str | None:
    """The Document text in the file at ``path``, or None after emitting a
    ``failed`` message that says the file is unreadable or empty."""
    try:
        text = path.read_text(encoding="utf-8")
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
    url: str | None,
    text_file: Path | None,
    transport: httpx.BaseTransport | None,
    sleep: Callable[[float], None],
) -> int:
    """Run both extraction passes over one Document and report them as messages.

    The Document is the article at ``url`` or the text in ``text_file``. Emits
    ``progress`` messages while it works, ``entities`` once Pass 1 is read, and
    then ``relationships`` (returning 0) or ``failed`` (returning 1). The key
    comes from the Provider's environment variable and is sent only to the
    Provider's host. Keeps no state and writes no files.
    """
    spec = PROVIDERS[provider]
    key = _key_from_environment(provider)
    if key is None:
        return 1

    def progress(stage: Stage, detail: str, pass_number: int | None = None) -> None:
        emit(
            Progress(
                provider=provider,
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

    def ask(pass_number: int, detail: str, messages: list[dict[str, str]]) -> str:
        def waiting(seconds: int, attempt: int, attempts: int) -> None:
            progress(
                "waiting_rate_limit",
                f"Rate limited; retrying in {seconds} s "
                f"(attempt {attempt} of {attempts})",
                pass_number,
            )

        progress(
            "calling_model",
            f"Pass {pass_number} of {PASS_COUNT}: {detail}",
            pass_number,
        )
        return chat_with_retries(
            provider,
            key,
            spec.default_model,
            messages,
            PASS1_MAX_TOKENS,
            on_wait=waiting,
            transport=transport,
            sleep=sleep,
        )

    try:
        reply = ask(
            1,
            f"calling {spec.default_model} on {len(body):,} characters",
            pass1_messages(body),
        )
        pass1 = parse_pass1(reply)
        entities = stamp_ids(pass1)
        emit(
            Entities(
                provider=provider,
                model=spec.default_model,
                document=DocumentSummary(url=location, title=title, chars=len(body)),
                entities=_proposed_entities(entities),
                dropped=pass1.dropped,
            )
        )
        reply = ask(
            2,
            f"calling {spec.default_model} for relationships",
            pass2_messages(body, entities),
        )
        links, dropped = known_relationships(parse_pass2(reply), entities)
    except ChatFailure as failure:
        emit(Failed(provider=provider, reason=failure.reason, message=failure.message))
        return 1
    except BadOutput as failure:
        emit(Failed(provider=provider, reason="bad_output", message=failure.message))
        return 1
    emit(
        Relationships(
            provider=provider,
            model=spec.default_model,
            relationships=[
                ProposedRelationship(
                    type=link.type,
                    from_id=link.from_id,
                    to_id=link.to_id,
                    date_from=link.date_from,
                    date_to=link.date_to,
                    date_precision=link.date_precision,
                    sentence=link.supporting_sentences[0],
                )
                for link in links
            ],
            dropped=dropped,
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
            url=args.url,
            text_file=args.text_file,
            transport=transport,
            sleep=sleep,
        )
    parser.print_help()
    return 0
