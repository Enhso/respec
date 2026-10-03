"""Command-line entry point for the Respec worker."""

import argparse
import os
import time
from collections.abc import Callable
from importlib.metadata import version
from typing import get_args

import httpx

from respec_worker.client import ChatFailure, chat, chat_with_retries
from respec_worker.extraction import (
    PASS1_MAX_TOKENS,
    BadOutput,
    parse_pass1,
    pass1_messages,
)
from respec_worker.fetch import FetchError, fetch_article
from respec_worker.messages import (
    DocumentSummary,
    Done,
    Entities,
    Failed,
    Progress,
    ProposedEntity,
    Stage,
    Started,
    emit,
)
from respec_worker.providers import PROVIDERS, Provider


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
            entities=[
                ProposedEntity(
                    label=entity.label,
                    name=entity.name,
                    sentence=entity.supporting_sentences[0],
                )
                for entity in response.entities
            ],
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
    ``test-call`` command makes one live chat call, and ``test-extraction`` runs
    Pass 1 over an article. ``transport`` replaces the network and ``sleep``
    replaces the rate-limit waits, in tests.
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
    args = parser.parse_args(argv)
    if args.command == "test-call":
        return run_test_call(args.provider, transport=transport)
    if args.command == "test-extraction":
        return run_test_extraction(
            args.provider, args.url, transport=transport, sleep=sleep
        )
    parser.print_help()
    return 0
