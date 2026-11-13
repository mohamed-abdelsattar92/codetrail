"""Refreshes a target's sources: mirror, rules, scan and materialization."""

from __future__ import annotations

from pathlib import Path

from codetrail.config import Paths, SourceSettings, check_containment, load_global, load_target
from codetrail.errors import CodetrailError
from codetrail.lock import target_in_use, target_lock
from codetrail.repo.mirror import read_file_at
from codetrail.repo.remote import fetch_target
from codetrail.repo.rules import TARGET_IGNORE_FILE, ExclusionRules
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest, build_source

# pathspec turns `*` into a backtracking `[^/]*` and a `**` part into `.*`, and the ways to match multiply: each `*`
# after the first in one part of a path, and each `**` part, is one more choice. Measured (pathspec 1.1.1): two choices
# take milliseconds on any path; three take a quarter of a second on one long name, and more take minutes. A property
# of the matcher, not a reader's choice.
MAX_WILDCARD_CHOICES = 2


def wildcard_choices(line: str) -> int:
    return sum(1 if part == "**" else max(0, part.count("*") - 1) for part in line.split("/"))


def target_rules(paths: Paths, name: str, mirror: Path, commit: str, limits: SourceSettings) -> ExclusionRules:
    """The exclusion rules at the commit: the target's own .codetrailignore there, then the founder's ignore file.

    Matching is timed against `max_ignore_match_seconds` only when the target has lines of its own."""
    target_file = read_file_at(mirror, commit, TARGET_IGNORE_FILE, max_bytes=limits.max_file_bytes)
    lines = ignore_lines(paths, name, target_file, limits.max_ignore_lines)
    budget = limits.max_ignore_match_seconds if target_file and target_file.strip() else None
    return ExclusionRules(lines, match_budget_seconds=budget)


def ignore_lines(paths: Paths, name: str, target_file: bytes | None, max_lines: int) -> list[str]:
    """The target's own .codetrailignore (from the commit), then the founder's ignore file.

    A target line the matcher can't match quickly fails the refresh, naming the line, and so does a target file with
    more than `max_lines` lines holding a pattern: dropping lines would show files the target meant to hide. Even
    within the wildcard limit, one line can cost about 2 ms on a crafted 255-byte name and 30 ms on a 4,096-byte path
    of them (pathspec 1.1.1), for every path, so their number is capped too."""
    lines = target_file.decode("utf-8", "replace").splitlines() if target_file else []
    patterns = sum(1 for line in lines if line.strip() and not line.startswith("#"))
    if patterns > max_lines:
        raise CodetrailError(
            f"The target's {TARGET_IGNORE_FILE} has {patterns} lines with a pattern; Codetrail reads at most "
            f"{max_lines} ([source] max_ignore_lines), since every line is matched against every path. Ask for the "
            "file to be shortened, or raise the limit in the configuration."
        )
    for number, line in enumerate(lines, start=1):
        if wildcard_choices(line) > MAX_WILDCARD_CHOICES:
            raise CodetrailError(
                f"The target's {TARGET_IGNORE_FILE}, line {number}, is too slow to match: Codetrail allows at most "
                f"{MAX_WILDCARD_CHOICES} of `**` parts and extra `*` in one part of a path, together. Ask for the "
                "line to be simplified, for example by splitting it into several lines."
            )
    founder_file = paths.ignore_file(name)
    if founder_file.exists():
        lines += founder_file.read_text(encoding="utf-8").splitlines()
    return lines


def refresh_source(paths: Paths, name: str) -> SourceManifest:
    """Refreshes the target's sources under its lock, after checking no folder would be made inside the target."""
    with target_in_use(paths, name):
        target = load_target(paths, name)
        if target.repository is not None:
            check_containment(paths, target.repository)
        with target_lock(paths, name):
            return refresh_while_locked(paths, name)


def refresh_while_locked(paths: Paths, name: str) -> SourceManifest:
    """Refreshes the target's sources; the caller holds the target's lock."""
    target = load_target(paths, name)
    if target.repository is not None:
        check_containment(paths, target.repository)
    settings = load_global(paths)
    scanner = SecretScanner(settings.tools)
    data = paths.target_data(name)
    mirror = data / "mirror.git"
    commit = fetch_target(mirror, target, stall_seconds=settings.hosting.timeout_seconds)
    rules = target_rules(paths, name, mirror, commit, settings.source)
    return build_source(mirror, commit, rules, scanner, data, settings.source)
