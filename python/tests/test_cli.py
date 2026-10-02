"""Tests for the worker command-line interface."""

from importlib.metadata import entry_points
from typing import get_args

import httpx
import pytest

from respec_worker.cli import main
from respec_worker.messages import MESSAGE_ADAPTER, Done, Failed, Started
from respec_worker.providers import PROVIDERS, Provider

KEY = "secret-key-0123456789"


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """`--version` prints the package version and exits with status 0."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "respec-worker 0.1.0\n"


def test_console_script_points_at_main() -> None:
    """The `respec-worker` console script resolves to `cli.main`."""
    (ep,) = [
        ep for ep in entry_points(group="console_scripts") if ep.name == "respec-worker"
    ]
    assert ep.load() is main


def _lines(text: str) -> list[Started | Done | Failed]:
    return [MESSAGE_ADAPTER.validate_json(line) for line in text.splitlines()]


@pytest.mark.parametrize("provider", get_args(Provider))
def test_test_call_success_emits_started_then_done(
    provider: Provider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A mocked success prints `started` then `done`, exits 0, and leaks no key."""
    spec = PROVIDERS[provider]
    monkeypatch.setenv(spec.key_env, KEY)
    answer = {"choices": [{"message": {"content": "pong"}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=answer))

    code = main(["test-call", "--provider", provider], transport=transport)

    captured = capsys.readouterr()
    assert code == 0
    assert _lines(captured.out) == [
        Started(provider=provider, model=spec.default_model),
        Done(provider=provider, model=spec.default_model, reply="pong"),
    ]
    assert KEY not in captured.out + captured.err


def test_test_call_failure_emits_started_then_failed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A mocked 401 prints `started` then `failed` with reason `auth`, exits 1,
    and neither the key nor the raw response body reaches stdout or stderr."""
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, text=f"bad key {KEY} RAW-BODY")
    )

    code = main(["test-call", "--provider", "openrouter"], transport=transport)

    captured = capsys.readouterr()
    assert code == 1
    started, failed = _lines(captured.out)
    assert isinstance(started, Started)
    assert isinstance(failed, Failed)
    assert failed.reason == "auth"
    assert KEY not in captured.out + captured.err
    assert "RAW-BODY" not in captured.out + captured.err


def test_test_call_without_a_key_fails_without_calling_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing key prints `failed` with reason `auth` and makes no request."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a call was made without a key")

    code = main(
        ["test-call", "--provider", "gemini"],
        transport=httpx.MockTransport(no_network),
    )

    (failed,) = _lines(capsys.readouterr().out)
    assert code == 1
    assert isinstance(failed, Failed)
    assert failed.reason == "auth"
    assert "No Google Gemini key is set" in failed.message


def test_test_call_rejects_an_unknown_provider() -> None:
    """argparse refuses a Provider outside the table."""
    with pytest.raises(SystemExit) as excinfo:
        main(["test-call", "--provider", "nowhere"])
    assert excinfo.value.code == 2


def test_no_arguments_prints_help_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no arguments the help lists the `test-call` command."""
    assert main([]) == 0
    assert "test-call" in capsys.readouterr().out
