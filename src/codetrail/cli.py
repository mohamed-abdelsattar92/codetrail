"""Codetrail's command line."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from types import FrameType

from codetrail import __version__
from codetrail.agents.export import refresh_export
from codetrail.agents.instructions import server_instructions, setup_text
from codetrail.agents.protocol import serve_stdio
from codetrail.agents.tools import AgentTools
from codetrail.assistant.estimate import UpdateEstimate, describe, tokens_text
from codetrail.assistant.status import provider_status
from codetrail.config import (
    PROVIDERS,
    Paths,
    load_global,
    load_target,
    load_team,
    validate_target_name,
    write_target,
)
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.facts import FactDiff
from codetrail.facts.store import FactStore
from codetrail.hosting.services import services_for
from codetrail.lock import target_in_use, team_in_use
from codetrail.metrics.history import values_before
from codetrail.metrics.report import Metric, build_report
from codetrail.metrics.repository import RepositoryReport, build_repository_report
from codetrail.remove import remove_target, remove_team
from codetrail.repo.mirror import check_branch
from codetrail.repo.refresh import refresh_source
from codetrail.repo.rules import Reason
from codetrail.server import serve
from codetrail.team.catch_up import CatchUpEstimate, describe_catch_up, run_catch_up
from codetrail.team.server import serve_team
from codetrail.team.settings import team_names, team_target_names, write_team
from codetrail.team.sync import TeamSync, sync_team
from codetrail.update import run_update
from codetrail.web.documentation import COUNTS, age, build_tiles, percent_of

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

    team = commands.add_parser("team", help="manage the teams, each following the repositories of its sources")
    team_commands = team.add_subparsers(dest="team_command", required=True)
    team_add = team_commands.add_parser("add", help="write a new team's settings and open its add-repositories screen")
    team_add.add_argument("name", help="a short name: lower-case letters, digits and hyphens")
    team_add.add_argument("--no-browser", action="store_true", help="print the sign-in link without opening it")
    team_commands.add_parser("list", help="show each team with its sources and target count")
    team_serve = team_commands.add_parser("serve", help="serve the team's page and every guide it follows on 127.0.0.1")
    team_serve.add_argument("name")
    team_serve.add_argument("--no-browser", action="store_true", help="print the sign-in link without opening it")
    team_update_command = team_commands.add_parser(
        "update", help="catch the whole team up: one estimate, one question, then every followed repository"
    )
    team_update_command.add_argument("name")
    team_update_command.add_argument("--yes", action="store_true", help="go ahead without asking")
    team_remove = team_commands.add_parser(
        "remove", help="delete a team and every target it created; nothing on the service is touched"
    )
    team_remove.add_argument("name")
    team_remove.add_argument("--yes", action="store_true", help="delete without asking")

    files = commands.add_parser("files", help="refresh a target's sources and list exactly what Codetrail can see")
    files.add_argument("name")

    update = commands.add_parser("update", help="refresh a target's sources and facts, and write the guide")
    update.add_argument("name")
    update.add_argument("--facts-only", action="store_true", help="refresh the facts without calling an assistant")
    update.add_argument("--yes", action="store_true", help="go ahead after showing the estimate, without asking")
    update.add_argument(
        "--retry-failed", action="store_true", help="write pages that failed last time, even if nothing in them changed"
    )

    metrics = commands.add_parser("metrics", help="show how well a target's repository explains itself")
    metrics.add_argument("name")

    stats = commands.add_parser("stats", help="show a target repository's history, releases, code size and facts")
    stats.add_argument("name")

    commands.add_parser("providers", help="show each assistant provider: installed, signed in, and how")

    agents = commands.add_parser("agents", help="rebuild the context for coding agents and print how to connect them")
    agents.add_argument("name")
    mcp = commands.add_parser("mcp", help="the tool server coding agents start over standard input and output")
    mcp.add_argument("name")
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
        if arguments.command == "team":
            return team_command(paths, arguments)
        if arguments.command == "target":
            return add_target(paths, arguments.name, arguments.path, arguments.branch)
        if arguments.command == "providers":
            return show_providers(paths)
        if arguments.command == "metrics":
            return show_metrics(paths, arguments.name)
        if arguments.command == "stats":
            return show_stats(paths, arguments.name)
        if arguments.command == "update":
            return update_target(paths, arguments.name, facts_only=arguments.facts_only, yes=arguments.yes,
                                 retry_failed=arguments.retry_failed)  # fmt: skip
        if arguments.command == "mcp":
            return serve_agent_tools(paths, arguments.name)
        if arguments.command == "agents":
            return connect_agents(paths, arguments.name)
        if arguments.command == "serve":
            serve(paths, arguments.name, open_browser=not arguments.no_browser)
            return 0
        return list_files(paths, arguments.name)
    except CodetrailError as error:
        print(f"codetrail: {error}", file=sys.stderr)
        return 1


def connect_agents(paths: Paths, name: str) -> int:
    folder = refresh_export(paths, name)
    print(setup_text(name, sys.executable, folder), end="")
    return 0


def serve_agent_tools(paths: Paths, name: str) -> int:
    """Runs the tool server until the agent closes standard input (design section 23.5)."""
    target = load_target(paths, name)
    tools = AgentTools(paths, name, load_global(paths).agents, target.location)
    serve_stdio(tools, server_instructions(name, target.location), sys.stdin.buffer, sys.stdout.buffer)
    return 0


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
    return _remove(
        f"target {name!r}",
        "The repository itself isn't touched.",
        yes,
        lambda confirm: remove_target(paths, name, confirm),
    )


def team_command(paths: Paths, arguments: argparse.Namespace) -> int:
    if arguments.team_command == "add":
        file = write_team(paths, arguments.name)
        print(f"Added team {arguments.name!r}. Settings: {file}")
        serve_team(paths, arguments.name, open_browser=not arguments.no_browser, start_path="/team/add")
        return 0
    if arguments.team_command == "list":
        return list_teams(paths)
    if arguments.team_command == "update":
        return team_update(paths, arguments.name, yes=arguments.yes)
    if arguments.team_command == "serve":
        serve_team(paths, arguments.name, open_browser=not arguments.no_browser)
        return 0
    return _remove(
        f"team {arguments.name!r}",
        "Nothing on the hosting services is touched.",
        arguments.yes,
        lambda confirm: remove_team(paths, arguments.name, confirm),
    )


def team_update(paths: Paths, name: str, yes: bool = False) -> int:
    """Catches the team up from the terminal, with the estimate and question of `codetrail update` (design 24.8)."""
    no_terminal = False
    stop = threading.Event()

    def confirm(estimate: CatchUpEstimate) -> list[str] | None:
        nonlocal no_terminal
        for line in describe_catch_up(estimate):
            print(printable(line))
        answer = ask_to_continue(yes)
        no_terminal = answer is None
        return [line.target for line in estimate.lines] if answer else None

    def sync() -> TeamSync:
        try:
            return sync_team(paths, name, services_for(load_global(paths)), datetime.now(UTC))
        except CodetrailError as error:
            print(f"Couldn't list the team's sources: {printable(str(error))}")
            return TeamSync()

    def progress(event: dict[str, object]) -> None:
        if event["step"] == "overview":
            print("Writing the team overview.")
        elif "repository" in event and (text := _catch_up_step(event)) is not None:
            print(f"{printable(str(event['repository']))}: {printable(text)}")

    with team_in_use(paths, name):
        result = run_catch_up(paths, name, confirm, progress, stop, sync)
    if result.declined:
        print("Nothing was spent." if no_terminal else "Nothing was updated.")
        return 2 if no_terminal else 0
    if result.overview is None:
        print("No overview was written.")
    elif result.overview.overview_id is None:
        print(f"No overview was written: {printable(result.overview.problem or 'it had a problem')}")
    else:
        print(f"Overview written ({result.overview.overview_id}).")
    return 0


def _catch_up_step(event: dict[str, object]) -> str | None:
    """One repository's step of a catch-up, as a line for the terminal, or None for a step it doesn't show: when it
    starts, each page written or not, its digest, and how it ended."""
    step, title = event["step"], event.get("title")
    if step == "facts":
        return "started, refreshing the facts (free)."
    if step == "page_written":
        return f"wrote “{title}”."
    if step == "page_failed":
        return f"“{title}” wasn't written: {event.get('reason')}"
    if step == "digest":
        return "writing the digest."
    if step == "committed":
        pages, failed = event.get("pages"), event.get("failed")
        saved = f"done, {_plural(pages, 'page')} saved to the guide" if isinstance(pages, int) else "done"
        return saved + (f", {failed} not written." if isinstance(failed, int) and failed else ".")
    if step == "target_failed":
        return f"not updated ({event.get('reason')})"
    return None


def list_teams(paths: Paths) -> int:
    for name in team_names(paths):
        count = len(team_target_names(paths, name))
        targets = _plural(count, "target")
        try:
            sources = [
                f"{source.service}:{source.source} ({source.follow})" for source in load_team(paths, name).sources
            ]
        except CodetrailError:
            print(f"{printable(name)}: its settings don't load - {targets}")
            continue
        print(f"{printable(name)}: {printable(', '.join(sources)) or 'no sources'} - {targets}")
    return 0


def _remove(what: str, reassurance: str, yes: bool, remove: Callable[[Callable[[list[Path]], bool]], bool]) -> int:
    """Shows what removing a target or a team deletes, asks unless `--yes`, and removes it."""
    no_terminal = False

    def confirm(locations: list[Path]) -> bool:
        nonlocal no_terminal
        print(f"Removing {what} deletes:")
        for location in locations:
            print(f"  {printable(str(location))}")
        print(reassurance)
        if yes:
            return True
        if not sys.stdin.isatty():
            no_terminal = True
            print("There's no terminal to ask in, so nothing was removed. Run again with --yes to go ahead.")
            return False
        return input(f"Remove {what} and delete these? [y/N] ").strip().lower() in ("y", "yes")

    if not remove(confirm):
        if not no_terminal:
            print("Nothing was removed.")
        return 2 if no_terminal else 0
    print(f"Removed {what}.")
    return 0


def printable(path: str) -> str:
    """Text from a target or a provider, quoted with escapes if it holds characters a terminal would act on."""
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
        # The sign-in method, models and messages come from the provider's own output.
        print(f"{provider:<12} {state:<10} {printable(how)}")
        if status.models:
            print(f"{'':<12} models: {printable(', '.join(status.models))}")
        if status.fix:
            print(f"{'':<12} {printable(status.fix)}")
        if status.warning:
            print(f"{'':<12} Note: {printable(status.warning)}")
    return 0


def ask_to_continue(yes: bool) -> bool | None:
    """Asks before paid work; None when there's no terminal to ask in (nothing is spent)."""
    if yes:
        return True
    if not sys.stdin.isatty():
        print("There's no terminal to ask in, so nothing was spent. Run again with --yes to go ahead.")
        return None
    return input("Continue? [y/N] ").strip().lower() in ("y", "yes")


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
        for line in describe(estimate):  # page titles come from the target, account lines from the providers
            print(printable(line))
        answer = ask_to_continue(yes)
        no_terminal = answer is None
        return bool(answer)

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


METRIC_LABELS = {
    Metric.DOCUMENTED_SHARE: "Documented rationale",
    Metric.COMMIT_WHY_SHARE: "Commits that explain why",
    Metric.ADR_ATTENTION: "ADRs needing attention",
    Metric.MENTIONED_SHARE: "Mentioned in a document",
    Metric.EXPLAINED_SHARE: "Explained by the guide",
}


def show_metrics(paths: Paths, name: str) -> int:
    """Prints each documentation metric with its change since the last update (design 18.4); writes nothing."""
    load_target(paths, name)
    database = paths.target_data(name) / "codetrail.db"
    with target_in_use(paths, name):
        if not database.exists():
            print(f"No facts yet. Run: codetrail update {name} --facts-only")
            return 1
        connection = connect(database)
        try:
            store = FactStore(connection)
            snapshot = store.latest_snapshot()
            if snapshot is None:
                print(f"No facts yet. Run: codetrail update {name} --facts-only")
                return 1
            report = build_report(paths, name, load_global(paths), store, date.today())
            tiles = build_tiles(report.values(), values_before(connection, snapshot.id), {})
        finally:
            connection.close()
    for metric, label in METRIC_LABELS.items():
        tile = tiles[metric]
        if tile.value is None:
            print(f"{label}: not available, the commit history couldn't be read")
        elif metric in (Metric.DOCUMENTED_SHARE, Metric.EXPLAINED_SHARE) and not report.rationale.pages:
            print(f"{label}: no pages yet")
        else:
            if metric in COUNTS:
                shown = str(tile.value.numerator)
            elif tile.percent is None:
                shown = "nothing to measure yet"
            else:
                shown = f"{tile.percent}% ({tile.value.numerator} of {tile.value.denominator})"
            unit = "" if metric in COUNTS else " points"
            change = "no earlier update" if tile.change is None else f"{tile.change:+d}{unit} since the last update"
            print(f"{label}: {shown}, {change}")
    return 0


def show_stats(paths: Paths, name: str) -> int:
    """Prints the repository statistics (design 19.5); reads the history but never writes it."""
    load_target(paths, name)
    database = paths.target_data(name) / "codetrail.db"
    settings = load_global(paths)
    with target_in_use(paths, name):
        if not database.exists():
            print(f"No facts yet. Run: codetrail update {name} --facts-only")
            return 1
        connection = connect(database)
        try:
            store = FactStore(connection)
            if store.latest_snapshot() is None:
                print(f"No facts yet. Run: codetrail update {name} --facts-only")
                return 1
            report = build_repository_report(paths, name, settings, store, date.today())
        finally:
            connection.close()
    print_stats(report, date.today(), settings.metrics.max_listed)
    return 0


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def print_stats(report: RepositoryReport, today: date, listed: int) -> None:
    activity, releases, size = report.activity, report.releases, report.size
    if activity is None or activity.first is None or activity.latest is None:
        print("History: not available, the commit history couldn't be read")
    else:
        years, months = age(activity.first, today)
        if years or months:
            ago = f"{_plural(years, 'year')}, {_plural(months, 'month')}"
        else:
            ago = _plural(max((today - activity.first).days, 0), "day")
        print(f"First commit: {activity.first.isoformat()} ({ago} ago)")
        print(f"Latest commit: {activity.latest.isoformat()}")
        print(f"Commits: {activity.commits} ({_plural(activity.merges, 'merge')})")
    if releases is None:
        print("Releases: not available, the tags couldn't be read")
    elif not releases.tags:
        print("Releases: none")
    else:
        latest = releases.tags[0]
        day = datetime.fromtimestamp(latest.date, UTC).date().isoformat()
        print(f"Releases: {len(releases.tags)}, the latest {printable(latest.name)} on {day}, "
              f"{_plural(releases.since_latest or 0, 'commit')} since")  # fmt: skip
    print(f"Code: {size.code_lines} lines in {_plural(len(size.languages), 'language')}")
    for language in size.languages[:listed]:
        print(f"  {language.language}: {_plural(language.files, 'file')}, {_plural(language.lines, 'line')}")
    print(f"Documents: {_plural(size.documents.files, 'file')}, {_plural(size.documents.lines, 'line')}")
    share = percent_of(report.values()[Metric.TEST_SHARE])
    shown = "nothing to measure" if share is None else f"{share}%"
    print(f"Tests: {shown} ({size.test_lines} of {size.code_lines} lines)")
    kinds = ", ".join(f"{kind.count} {kind.kind}" for kind in report.inventory.kinds)
    print(f"Facts: {kinds or 'none'}")


def _change_counts(diff: FactDiff) -> dict[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for position, ids in enumerate((diff.added_entities, diff.changed_entities, diff.removed_entities)):
        for entity_id in ids:
            counts.setdefault(entity_id.split(":", 1)[0], [0, 0, 0])[position] += 1
    for position, keys in enumerate((diff.added_relations, diff.changed_relations, diff.removed_relations)):
        for key in keys:
            counts.setdefault(key[1], [0, 0, 0])[position] += 1
    return counts
