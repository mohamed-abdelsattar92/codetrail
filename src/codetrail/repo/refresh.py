"""Refreshes a target's sources: mirror, rules, scan and materialization."""

from __future__ import annotations

from codetrail.config import Paths, check_containment, load_global, load_target
from codetrail.errors import CodetrailError
from codetrail.lock import target_in_use, target_lock
from codetrail.repo.mirror import read_file_at
from codetrail.repo.remote import fetch_target
from codetrail.repo.rules import ExclusionRules
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest, build_source

TARGET_IGNORE_FILE = ".codetrailignore"
# pathspec turns each `*` into a backtracking `[^/]*`: one long name takes 2 ms against three in a segment, 0.25 s
# against four and minutes against six (pathspec 1.1.1). A property of the matcher, not a reader's choice.
MAX_WILDCARDS_PER_SEGMENT = 3


def ignore_lines(paths: Paths, name: str, target_file: bytes | None) -> list[str]:
    """The target's own .codetrailignore (from the commit), then the founder's ignore file.

    A target line with more `*` in one segment than the matcher handles quickly fails the refresh, naming the line:
    dropping it would show files the target meant to hide."""
    lines = target_file.decode("utf-8", "replace").splitlines() if target_file else []
    for number, line in enumerate(lines, start=1):
        if any(segment != "**" and segment.count("*") > MAX_WILDCARDS_PER_SEGMENT for segment in line.split("/")):
            raise CodetrailError(
                f"The target's {TARGET_IGNORE_FILE}, line {number}, has more than {MAX_WILDCARDS_PER_SEGMENT} `*` "
                f"in one part of its path; Codetrail matches at most {MAX_WILDCARDS_PER_SEGMENT} quickly. Ask for "
                "the line to be simplified, for example by splitting it."
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
    rules = ExclusionRules(ignore_lines(paths, name, read_file_at(mirror, commit, TARGET_IGNORE_FILE)))
    return build_source(mirror, commit, rules, scanner, data)
