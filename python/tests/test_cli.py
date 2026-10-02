"""Tests for the worker command-line interface."""

from importlib.metadata import entry_points

import pytest

from respec_worker.cli import main


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
