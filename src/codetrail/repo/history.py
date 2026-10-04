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
RECORD, FIELD = "\x1e", "\x1f"


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
    """The commits after `start` up to `end`, oldest first; merges list the files they bring to the first parent."""
    output = run_git(
        ["-c", "core.quotePath=false", "log", "--reverse", "--diff-merges=first-parent", "--name-only",
         f"--format={RECORD}%H{FIELD}%an{FIELD}%aI{FIELD}%P{FIELD}%s{FIELD}%b{FIELD}",
         end if start is None else f"{start}..{end}"],
        git_dir=mirror,
    ).decode("utf-8", "replace")  # fmt: skip
    commits = []
    for record in output.split(RECORD)[1:]:
        sha, author, date, parents, subject, body, names = record.split(FIELD)
        files = [name for name in names.strip().splitlines() if name and not name.startswith('"') and visible(name)]
        commits.append(Commit(sha, author, date, subject, body.strip(), files, len(parents.split()) > 1))
    return _withhold_flagged_messages(commits, scanner)


def merges_between(mirror: Path, start: str, end: str) -> int:
    count = run_git(["rev-list", "--count", "--merges", "--first-parent", f"{start}..{end}"], git_dir=mirror)
    return int(count)


def diff_between(
    mirror: Path, start: str, end: str, visible: Callable[[str], bool], scanner: SecretScanner
) -> list[FileDiff]:
    output = run_git(["diff", "--name-status", "--no-renames", "-z", start, end], git_dir=mirror)
    fields = output.decode("utf-8", "surrogateescape").split("\0")
    changes = [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if visible(fields[i + 1])]
    patches = [
        run_git(["diff", "--no-renames", start, end, "--", f":(literal){path}"], git_dir=mirror).decode(
            "utf-8", "replace"
        )
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
