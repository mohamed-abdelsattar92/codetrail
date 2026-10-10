"""The materialized sources: only the files Codetrail may see, at one commit (design sections 3.2 to 3.4).

`source/` is rebuilt in full on every refresh: written into `source.next/`, scanned with gitleaks, cleared of what it
flags, then swapped into place. If anything fails, `source.next/` is removed and `source/` stays as it was.
Symlinks, submodules and unsafe paths are never written.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from codetrail.repo.mirror import list_tree, read_blobs
from codetrail.repo.rules import ExclusionRules, Reason, on_disk_key
from codetrail.repo.secrets import SecretScanner

REGULAR_FILE_MODES = {"100644", "100755"}


@dataclass(frozen=True)
class Excluded:
    path: str
    reason: Reason
    rule: str | None = None


@dataclass(frozen=True)
class SourceManifest:
    commit: str
    files: dict[str, str]  # path -> blob
    excluded: list[Excluded] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=1, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SourceManifest | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        excluded = [Excluded(item["path"], Reason(item["reason"]), item["rule"]) for item in data["excluded"]]
        return cls(commit=data["commit"], files=data["files"], excluded=excluded)

    def excluded_paths(self) -> set[str]:
        return {item.path for item in self.excluded}


def safe_path(path: str) -> bool:
    """A relative path with no empty, `.`, `..` or `.git` parts (in any case), which stays inside its folder."""
    if not path or path.startswith("/") or "\\" in path:
        return False
    if any("\udc80" <= character <= "\udcff" for character in path):  # bytes that aren't UTF-8 (surrogateescape)
        return False
    parts = path.split("/")
    return all(part and part not in (".", "..") and on_disk_key(part) != ".git" for part in parts)


def allowed_reader(source: Path, files: Mapping[str, str], max_bytes: int) -> Callable[[str], bytes | None]:
    """A reader of allowed files only: a listed path, inside `source/`, a plain file within the size cap.

    The system pass (design 17.3) and the documentation metrics (design 18.2) read files only through it.
    """
    root = source.resolve()

    def read(path: str) -> bytes | None:
        if path not in files:
            return None
        file = source / PurePosixPath(path)
        try:
            if file.is_symlink() or not file.resolve().is_relative_to(root) or not file.is_file():
                return None
            return file.read_bytes() if file.stat().st_size <= max_bytes else None
        except OSError:
            return None

    return read


def build_source(
    mirror: Path, commit: str, rules: ExclusionRules, scanner: SecretScanner, data_dir: Path
) -> SourceManifest:
    source, next_source = data_dir / "source", data_dir / "source.next"
    shutil.rmtree(next_source, ignore_errors=True)
    next_source.mkdir(parents=True)
    try:
        files: dict[str, str] = {}
        excluded: list[Excluded] = []
        entries = list_tree(mirror, commit)
        colliding = _colliding_paths([entry.path for entry in entries])
        for entry in entries:
            reason = Reason.UNSAFE_PATH if entry.path in colliding else _reason(entry.mode, entry.path, rules)
            if reason is None:
                files[entry.path] = entry.blob
            else:
                excluded.append(Excluded(entry.path, reason))
        paths_by_blob: dict[str, list[str]] = {}
        for path, blob in files.items():
            paths_by_blob.setdefault(blob, []).append(path)
        for blob, content in read_blobs(mirror, list(paths_by_blob)):
            for path in paths_by_blob[blob]:
                destination = next_source / PurePosixPath(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        flagged: dict[str, str] = {}
        for finding in scanner.scan_directory(next_source):
            flagged.setdefault(finding.path, finding.rule)
        for path, rule in sorted(flagged.items()):
            (next_source / PurePosixPath(path)).unlink()
            del files[path]
            excluded.append(Excluded(path, Reason.GITLEAKS, rule))
        manifest = SourceManifest(
            commit=commit, files=dict(sorted(files.items())), excluded=sorted(excluded, key=lambda item: item.path)
        )
        _swap(next_source, source)
        manifest.save(data_dir / "source.json")
        return manifest
    except BaseException:
        shutil.rmtree(next_source, ignore_errors=True)
        raise


def _colliding_paths(paths: list[str]) -> set[str]:
    """Paths that would share a file or folder on a case-insensitive, normalizing file system.

    Each path and each of its parent folders is a name on disk; two different spellings of one name collide, and
    every path under either spelling is excluded (A.txt with a.txt, Docs/a.md with docs/b.md, Notes with notes/x).
    """
    spellings: dict[str, set[str]] = {}
    for path in paths:
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            spellings.setdefault(on_disk_key(prefix), set()).add(prefix)
    clashing = {key for key, names in spellings.items() if len(names) > 1}
    colliding = set()
    for path in paths:
        parts = path.split("/")
        if any(on_disk_key("/".join(parts[:end])) in clashing for end in range(1, len(parts) + 1)):
            colliding.add(path)
    return colliding


def _reason(mode: str, path: str, rules: ExclusionRules) -> Reason | None:
    if not safe_path(path):
        return Reason.UNSAFE_PATH
    if mode not in REGULAR_FILE_MODES:
        return Reason.NOT_A_FILE
    return rules.reason(path)


def _swap(next_source: Path, source: Path) -> None:
    old = source.with_name("source.old")
    shutil.rmtree(old, ignore_errors=True)
    if source.exists():
        source.rename(old)
    next_source.rename(source)
    shutil.rmtree(old, ignore_errors=True)
