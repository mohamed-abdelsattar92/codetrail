"""Refreshes a target's sources: mirror, rules, scan and materialization."""

from __future__ import annotations

from codetrail.config import Paths, check_containment, load_global, load_target
from codetrail.lock import target_lock
from codetrail.repo.mirror import read_file_at, refresh_mirror
from codetrail.repo.rules import ExclusionRules
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest, build_source

TARGET_IGNORE_FILE = ".codetrailignore"


def ignore_lines(paths: Paths, name: str, target_file: bytes | None) -> list[str]:
    """The target's own .codetrailignore (from the commit), then the founder's ignore file."""
    lines = target_file.decode("utf-8", "replace").splitlines() if target_file else []
    founder_file = paths.ignore_file(name)
    if founder_file.exists():
        lines += founder_file.read_text(encoding="utf-8").splitlines()
    return lines


def refresh_source(paths: Paths, name: str) -> SourceManifest:
    """Refreshes the target's sources under its lock."""
    with target_lock(paths, name):
        return refresh_while_locked(paths, name)


def refresh_while_locked(paths: Paths, name: str) -> SourceManifest:
    """Refreshes the target's sources; the caller holds the target's lock."""
    target = load_target(paths, name)
    check_containment(paths, target.repository)
    scanner = SecretScanner(load_global(paths).tools.gitleaks)
    data = paths.target_data(name)
    mirror = data / "mirror.git"
    commit = refresh_mirror(mirror, target.repository, target.branch)
    rules = ExclusionRules(ignore_lines(paths, name, read_file_at(mirror, commit, TARGET_IGNORE_FILE)))
    return build_source(mirror, commit, rules, scanner, data)
