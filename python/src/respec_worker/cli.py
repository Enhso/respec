"""Command-line entry point for the Respec worker."""

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> int:
    """Run the worker CLI and return the process exit code.

    With no arguments, print the help text to stdout and return 0. The
    ``--version`` flag prints the installed package version and exits.
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
    parser.parse_args(argv)
    parser.print_help()
    return 0
