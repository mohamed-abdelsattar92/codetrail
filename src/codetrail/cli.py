"""Codetrail's command line."""

import argparse
import sys

from codetrail import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codetrail", description="Turn a git repository into a local learning guide.")
    parser.add_argument("--version", action="version", version=f"codetrail {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help(file=sys.stderr)
        return 2
    return 0
