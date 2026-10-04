"""Builds small git repositories for tests, and proves they stay untouched (design section 11).

Nothing binary is committed: each test builds its repository in a temporary folder.
"""

from __future__ import annotations

import hashlib
import os
import random
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
}


@dataclass(frozen=True)
class Symlink:
    target: str


FileContent = str | bytes | Symlink | None
Commit = dict[str, FileContent]


def git(repository: Path, *arguments: str, date: int = 0) -> str:
    stamp = f"{1_790_000_000 + date * 60} +0000"
    environment = {**os.environ, **GIT_ENVIRONMENT, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments], env=environment, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def write_commit(repository: Path, files: Commit, message: str, date: int) -> str:
    for path, content in files.items():
        file = repository / path
        if content is None:
            git(repository, "rm", "-q", "--", path)
            continue
        file.parent.mkdir(parents=True, exist_ok=True)
        if file.is_symlink() or file.exists():
            file.unlink()
        if isinstance(content, Symlink):
            file.symlink_to(content.target)
        elif isinstance(content, bytes):
            file.write_bytes(content)
        else:
            file.write_text(content)
        git(repository, "add", "-f", "--", path)
    git(repository, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return git(repository, "rev-parse", "HEAD")


def make_repository(root: Path, commits: list[Commit], branch: str = "develop") -> Path:
    """A checkout at `root` with one commit per dictionary; a None value deletes the file."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", branch)
    for number, files in enumerate(commits):
        write_commit(root, files, f"Commit {number}", number)
    return root


def add_commit(repository: Path, files: Commit, message: str = "Another commit") -> str:
    count = int(git(repository, "rev-list", "--count", "HEAD"))
    return write_commit(repository, files, message, count)


def snapshot_tree(root: Path) -> dict[str, tuple[int, int, int, int, str]]:
    """Every file and folder under `root`, .git included: size, mtime, inode, link count and content hash."""
    snapshot: dict[str, tuple[int, int, int, int, str]] = {}
    for folder, folders, files in os.walk(root):
        for name in folders + files:
            path = Path(folder) / name
            status = path.lstat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() else ""
            snapshot[str(path.relative_to(root))] = (
                status.st_size if not path.is_dir() else 0,
                status.st_mtime_ns,
                status.st_ino,
                status.st_nlink,
                digest,
            )
    return snapshot


def fake_github_token(seed: int = 7) -> str:
    """A string shaped like a GitHub token, assembled at runtime so no secret is ever committed."""
    alphabet = string.ascii_letters + string.digits
    generator = random.Random(seed)  # noqa: S311 - a test value, not a secret
    rest = "".join(generator.choice(alphabet) for _ in range(36))
    return "gh" + "p_" + rest
