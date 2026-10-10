"""A target's history, filtered: logs and diffs never show excluded paths or secrets (design section 3.3).

Commit messages and diffs are scanned with gitleaks as text. A flagged message is withheld, and so is a file's patch
when it holds a finding, which catches a secret that a commit removed although the file is clean now.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from codetrail.repo.git import run_git
from codetrail.repo.secrets import SecretScanner

WITHHELD_MESSAGE = "[withheld: gitleaks flagged this message]"


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    date: str
    subject: str
    body: str
    files: list[str]
    is_merge: bool


@dataclass(frozen=True)
class FileDiff:
    path: str
    status: str
    patch: str | None  # None when gitleaks flagged it


def commits_between(
    mirror: Path, start: str | None, end: str, visible: Callable[[str], bool], scanner: SecretScanner
) -> list[Commit]:
    """The commits after `start` up to `end`, oldest first; merges list the files they bring to the first parent.

    Each commit is read on its own, with NUL between fields (git messages can't hold NUL), so no text in a message
    can forge or break a record.
    """
    revision_range = end if start is None else f"{start}..{end}"
    revisions = run_git(["rev-list", "--reverse", "--end-of-options", revision_range], git_dir=mirror)
    commits = [_read_commit(mirror, sha, visible) for sha in revisions.decode().split()]
    return _withhold_flagged_messages(commits, scanner)


def _read_commit(mirror: Path, sha: str, visible: Callable[[str], bool]) -> Commit:
    header = run_git(["show", "-s", "--format=%H%x00%an%x00%aI%x00%P%x00%s%x00%b", sha], git_dir=mirror)
    commit_sha, author, date, parents, subject, body = header.decode("utf-8", "replace").split("\0", 5)
    parent_list = parents.split()
    changed = ["diff-tree", "--no-commit-id", "-r", "-z", "--name-only"]
    changed += [parent_list[0], sha] if parent_list else ["--root", sha]
    names = run_git(changed, git_dir=mirror).decode("utf-8", "surrogateescape").split("\0")
    files = [name for name in names if name and visible(name)]
    return Commit(commit_sha, author, date, subject, body.strip(), files, len(parent_list) > 1)


LOG_FIELDS = 5  # sha, author, date, subject, body


def latest_commits(mirror: Path, end: str, limit: int, scanner: SecretScanner) -> list[Commit]:
    """The latest `limit` non-merge commits up to `end`, newest first, with flagged messages withheld.

    One git call: fields and records are separated by NUL, which no message can hold. Files aren't listed.
    """
    output = run_git(
        ["log", f"-n{limit}", "--no-merges", "-z", "--format=%H%x00%an%x00%aI%x00%s%x00%b", "--end-of-options", end],
        git_dir=mirror,
    ).decode("utf-8", "replace")
    fields = output.split("\0")
    records = (fields[start : start + LOG_FIELDS] for start in range(0, len(fields) - LOG_FIELDS + 1, LOG_FIELDS))
    commits = [
        Commit(sha, author, date, subject, body.strip(), [], False) for sha, author, date, subject, body in records
    ]
    return _withhold_flagged_messages(commits, scanner)


def merges_between(mirror: Path, start: str, end: str) -> int:
    count = run_git(["rev-list", "--count", "--merges", "--first-parent", "--end-of-options", f"{start}..{end}"],
                    git_dir=mirror)  # fmt: skip
    return int(count)


def diff_between(
    mirror: Path, start: str, end: str, visible: Callable[[str], bool], scanner: SecretScanner
) -> list[FileDiff]:
    output = run_git(["diff", "--name-status", "--no-renames", "-z", "--end-of-options", start, end], git_dir=mirror)
    fields = output.decode("utf-8", "surrogateescape").split("\0")
    changes = [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if visible(fields[i + 1])]
    patches = [
        run_git(
            ["diff", "--no-renames", "--end-of-options", start, end, "--", f":(literal){path}"], git_dir=mirror
        ).decode("utf-8", "replace")
        for _status, path in changes
    ]
    flagged = _flagged_indexes(patches, scanner)
    return [
        FileDiff(path, status, None if index in flagged else patch)
        for index, ((status, path), patch) in enumerate(zip(changes, patches, strict=True))
    ]


def _withhold_flagged_messages(commits: list[Commit], scanner: SecretScanner) -> list[Commit]:
    flagged = _flagged_indexes([f"{commit.subject}\n{commit.body}" for commit in commits], scanner)
    return [
        Commit(c.sha, c.author, c.date, WITHHELD_MESSAGE, "", c.files, c.is_merge) if index in flagged else c
        for index, c in enumerate(commits)
    ]


def _flagged_indexes(texts: Sequence[str], scanner: SecretScanner) -> set[int]:
    """Scans all texts in one gitleaks run and returns the indexes of those holding a finding."""
    if not texts:
        return set()
    first_lines, line = [], 1
    for text in texts:
        first_lines.append(line)
        line += text.count("\n") + 1
    combined = "\n".join(texts)
    flagged = set()
    for finding in scanner.scan_text(combined):
        index = max(i for i, first in enumerate(first_lines) if first <= finding.line)
        flagged.add(index)
    return flagged


def commit_count(mirror: Path, start: str, end: str) -> int:
    return int(run_git(["rev-list", "--count", "--end-of-options", f"{start}..{end}"], git_dir=mirror))


def changed_paths(mirror: Path, start: str, end: str) -> list[str]:
    """Paths changed between two commits; only names, which callers filter before showing."""
    output = run_git(["diff", "--name-only", "--no-renames", "-z", "--end-of-options", start, end], git_dir=mirror)
    return [name for name in output.decode("utf-8", "surrogateescape").split("\0") if name]


def recent_commits(
    mirror: Path,
    end: str,
    paths: Sequence[str],
    limit: int,
    visible: Callable[[str], bool],
    scanner: SecretScanner,
) -> list[Commit]:
    """The latest commits (at most `limit`, oldest first) that touched any of `paths`, filtered like commits_between."""
    pathspecs = [f":(literal){path}" for path in paths if path] or ["."]
    shas = run_git(["log", f"-n{limit}", "--format=%H", "--end-of-options", end, "--", *pathspecs],
                   git_dir=mirror).decode().split()  # fmt: skip
    commits = [_read_commit(mirror, sha, visible) for sha in reversed(shas)]
    return _withhold_flagged_messages(commits, scanner)
