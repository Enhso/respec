"""Command-line entry point for the Respec worker."""

import argparse
import os
from importlib.metadata import version
from typing import get_args

import httpx

from respec_worker.client import ChatFailure, chat
from respec_worker.messages import Done, Failed, Started, emit
from respec_worker.providers import PROVIDERS, Provider


def run_test_call(provider: Provider, *, transport: httpx.BaseTransport | None) -> int:
    """Send one small chat request to ``provider`` and report it as messages.

    The key comes from the Provider's environment variable. Emits ``started``,
    then ``done`` (returning 0) or ``failed`` (returning 1). Keeps no state and
    writes no files.
    """
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


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    """Run the worker CLI and return the process exit code.

    With no arguments, print the help text to stdout and return 0. The
    ``--version`` flag prints the installed package version and exits. The
    ``test-call`` command makes one live chat call; ``transport`` replaces the
    network in tests.
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
    args = parser.parse_args(argv)
    if args.command == "test-call":
        return run_test_call(args.provider, transport=transport)
    parser.print_help()
    return 0
