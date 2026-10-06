"""Codetrail's command line."""

from __future__ import annotations

import argparse
import signal
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from types import FrameType

from codetrail import __version__
from codetrail.assistant.estimate import UpdateEstimate, describe, tokens_text
from codetrail.assistant.status import provider_status
from codetrail.config import PROVIDERS, Paths, load_global, validate_target_name, write_target
from codetrail.errors import CodetrailError
from codetrail.facts import FactDiff
from codetrail.remove import remove_target
from codetrail.repo.mirror import check_branch
from codetrail.repo.refresh import refresh_source
from codetrail.repo.rules import Reason
from codetrail.server import serve
from codetrail.update import run_update

stopping = False  # set once a hangup or terminate signal has started Codetrail's exit


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
    remove = target_commands.add_parser(
        "remove", help="delete everything Codetrail keeps for a target; the repository isn't touched"
    )
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="delete without asking")

    files = commands.add_parser("files", help="refresh a target's sources and list exactly what Codetrail can see")
    files.add_argument("name")

    update = commands.add_parser("update", help="refresh a target's sources and facts, and write the guide")
    update.add_argument("name")
    update.add_argument("--facts-only", action="store_true", help="refresh the facts without calling an assistant")
    update.add_argument("--yes", action="store_true", help="go ahead after showing the estimate, without asking")
    update.add_argument(
        "--retry-failed", action="store_true", help="write pages that failed last time, even if nothing in them changed"
    )

    commands.add_parser("providers", help="show each assistant provider: installed, signed in, and how")

    serve_command = commands.add_parser("serve", help="serve the guide's page on 127.0.0.1")
    serve_command.add_argument("name")
    serve_command.add_argument("--no-browser", action="store_true", help="print the sign-in link without opening it")
    return parser


def main(argv: list[str] | None = None) -> int:
    for signal_number in (signal.SIGHUP, signal.SIGTERM):
        if signal.getsignal(signal_number) == signal.SIG_DFL:  # one ignored at start (nohup) stays ignored
            signal.signal(signal_number, stop_on_signal)
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help(file=sys.stderr)
        return 2
    paths = Paths.from_environment()
    try:
        if arguments.command == "target" and arguments.target_command == "remove":
            return remove_target_command(paths, arguments.name, yes=arguments.yes)
        if arguments.command == "target":
            return add_target(paths, arguments.name, arguments.path, arguments.branch)
        if arguments.command == "providers":
            return show_providers(paths)
        if arguments.command == "update":
            return update_target(paths, arguments.name, facts_only=arguments.facts_only, yes=arguments.yes,
                                 retry_failed=arguments.retry_failed)  # fmt: skip
        if arguments.command == "serve":
            serve(paths, arguments.name, open_browser=not arguments.no_browser)
            return 0
        return list_files(paths, arguments.name)
    except CodetrailError as error:
        print(f"codetrail: {error}", file=sys.stderr)
        return 1


def stop_on_signal(signal_number: int, _frame: FrameType | None) -> None:
    """Turns a hangup or terminate signal into an exception, so the cleanup that stops child programs runs.

    Child programs run in their own session (design section 15.5), so these signals don't reach them; by default
    Python would die without unwinding and leave them running. It fires once: a second signal (closing a terminal can
    send two) mustn't interrupt the cleanup. The signals stay caught rather than ignored, since an ignored signal stays
    ignored in any program the cleanup starts.
    """
    global stopping
    if stopping:
        return
    stopping = True
    raise SystemExit(128 + signal_number)


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


def remove_target_command(paths: Paths, name: str, yes: bool = False) -> int:
    no_terminal = False

    def confirm(locations: list[Path]) -> bool:
        nonlocal no_terminal
        print(f"Removing target {name!r} deletes:")
        for location in locations:
            print(f"  {printable(str(location))}")
        print("The repository itself isn't touched.")
        if yes:
            return True
        if not sys.stdin.isatty():
            no_terminal = True
            print("There's no terminal to ask in, so nothing was removed. Run again with --yes to go ahead.")
            return False
        return input(f"Remove target {name!r} and delete these? [y/N] ").strip().lower() in ("y", "yes")

    if not remove_target(paths, name, confirm):
        if not no_terminal:
            print("Nothing was removed.")
        return 2 if no_terminal else 0
    print(f"Removed target {name!r}.")
    return 0


def printable(path: str) -> str:
    """A path from a target, quoted with escapes if it holds characters a terminal would act on."""
    if any(unicodedata.category(character) in ("Cc", "Cf", "Cs", "Zl", "Zp") for character in path):
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


def show_providers(paths: Paths) -> int:
    settings = load_global(paths)
    for provider in PROVIDERS:
        status = provider_status(provider, settings)
        state = "ready" if status.ready else "not ready"
        how = " · ".join(part for part in (status.method, status.program) if part)
        print(f"{provider:<12} {state:<10} {how}")
        if status.models:
            print(f"{'':<12} models: {', '.join(status.models)}")
        if status.fix:
            print(f"{'':<12} {status.fix}")
        if status.warning:
            print(f"{'':<12} Note: {status.warning}")
    return 0


def update_target(
    paths: Paths, name: str, facts_only: bool = False, yes: bool = False, retry_failed: bool = False
) -> int:
    no_terminal = False

    def confirm(estimate: UpdateEstimate) -> bool:
        """Shows the estimate and asks before any paid work (design section 15.4)."""
        nonlocal no_terminal
        if not estimate.lines:
            print("Nothing for the assistant to do in this update.")
            return True
        for line in describe(estimate):
            print(line)
        if yes:
            return True
        if not sys.stdin.isatty():
            no_terminal = True
            print("There's no terminal to ask in, so nothing was spent. Run again with --yes to go ahead.")
            return False
        return input("Continue? [y/N] ").strip().lower() in ("y", "yes")

    result = run_update(paths, name, facts_only=facts_only, confirm=None if facts_only else confirm,
                        retry_failed=retry_failed)  # fmt: skip
    extraction, diff = result.extraction, result.diff
    print(f"Updated {name} at commit {result.manifest.commit[:12]} (snapshot {result.snapshot.id}).")
    print(f"Facts: {len(extraction.entities)} entities, {len(extraction.relations)} relations.")
    if extraction.system:
        print(
            "System connections: " + ", ".join(f"{rule} {count}" for rule, count in sorted(extraction.system.items()))
        )
    if diff.is_empty:
        print("No changes since the last update.")
    else:
        print("Changes (+ added, ~ changed, - removed):")
        for kind, counts in sorted(_change_counts(diff).items()):
            print(f"  {kind}: +{counts[0]} ~{counts[1]} -{counts[2]}")
    for extractor, count in sorted(extraction.unresolved.items()):
        print(f"Unresolved references ({extractor}): {count}")
    for warning in extraction.warnings:
        print(f"Warning: {printable(warning)}")
    if result.declined:
        print("The facts were refreshed; the guide wasn't updated.")
        return 2 if no_terminal else 0
    generation = result.generation
    if generation is not None:
        print(
            f"Guide: {len(generation.written)} pages written, {len(generation.failed)} failed, "
            f"{len(generation.left_for_later)} left for the next update; "
            f"used ~{tokens_text(generation.tokens)} tokens, ${generation.cost_usd:.2f} at API prices."
        )
        for page, reason in generation.failed:
            print(f"  Not rewritten: {page} ({printable(reason)})")
        for page in generation.skipped:
            print(f"  Skipped: {page} (it failed last time, and nothing in it changed since)")
        if generation.skipped:
            print(f"  To write them anyway: codetrail update {name} --retry-failed")
        for problem in generation.outline_problems:
            print(f"  Outline: {printable(problem)}")
        if generation.digest:
            print(f"  Digest: {generation.digest}")
    return 0


def _change_counts(diff: FactDiff) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for position, ids in enumerate((diff.added_entities, diff.changed_entities, diff.removed_entities)):
        for entity_id in ids:
            counts.setdefault(entity_id.split(":", 1)[0], [0, 0, 0])[position] += 1
    for position, keys in enumerate((diff.added_relations, diff.changed_relations, diff.removed_relations)):
        for key in keys:
            counts.setdefault(key[1], [0, 0, 0])[position] += 1
    return counts
