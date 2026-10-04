"""Codetrail's command line."""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from codetrail import __version__
from codetrail.config import Paths, validate_target_name, write_target
from codetrail.errors import CodetrailError
from codetrail.repo.mirror import check_branch
from codetrail.repo.refresh import refresh_source
from codetrail.repo.rules import Reason


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codetrail", description="Turn a git repository into a local learning guide.")
    parser.add_argument("--version", action="version", version=f"codetrail {__version__}")
    commands = parser.add_subparsers(dest="command")

    target = commands.add_parser("target", help="manage the repositories Codetrail teaches")
    target_commands = target.add_subparsers(dest="target_command", required=True)
    add = target_commands.add_parser("add", help="register a repository; nothing is written into it")
    add.add_argument("name", help="a short name: lower-case letters, digits and hyphens")
    add.add_argument("path", type=Path, help="the repository's local checkout")
    add.add_argument("--branch", default="develop", help="the branch to teach (default: develop)")

    files = commands.add_parser("files", help="refresh a target's sources and list exactly what Codetrail can see")
    files.add_argument("name")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help(file=sys.stderr)
        return 2
    paths = Paths.from_environment()
    try:
        if arguments.command == "target":
            return add_target(paths, arguments.name, arguments.path, arguments.branch)
        return list_files(paths, arguments.name)
    except CodetrailError as error:
        print(f"codetrail: {error}", file=sys.stderr)
        return 1


def add_target(paths: Paths, name: str, path: Path, branch: str) -> int:
    validate_target_name(name)
    path = path.expanduser().resolve()
    if not (path / ".git").exists():
        raise CodetrailError(f"{path} isn't a git repository (it has no .git).")
    check_branch(path, branch)
    file = write_target(paths, name, path, branch)
    print(f"Added target {name!r}: {path} ({branch}). Settings: {file}")
    print(f"Exclusions you add go in {paths.ignore_file(name)} (gitignore syntax).")
    return 0


def printable(path: str) -> str:
    """A path from a target, quoted with escapes if it holds characters a terminal would act on."""
    if any(unicodedata.category(character) in ("Cc", "Cf") for character in path):
        return repr(path)
    return path


def list_files(paths: Paths, name: str) -> int:
    manifest = refresh_source(paths, name)
    for path in manifest.files:
        print(printable(path))
    print("Excluded:")
    for item in manifest.excluded:
        reason = f"{item.reason} ({item.rule})" if item.rule else str(item.reason)
        print(f"{reason}\t{printable(item.path)}")
    counts = Counter(item.reason for item in manifest.excluded)
    breakdown = ", ".join(f"{counts[reason]} {reason}" for reason in Reason if counts[reason])
    print(
        f"{len(manifest.files)} visible, {len(manifest.excluded)} excluded ({breakdown or 'none'}), "
        f"commit {manifest.commit[:12]}"
    )
    return 0
