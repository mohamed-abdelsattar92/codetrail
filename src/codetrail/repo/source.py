"""The materialized sources: only the files Codetrail may see, at one commit (design sections 3.2 to 3.4).

`source/` is rebuilt in full on every refresh: written into `source.next/`, scanned with gitleaks, cleared of what it
flags, then swapped into place. If anything fails, `source.next/` is removed and `source/` stays as it was.
Symlinks, submodules and unsafe paths are never written.
"""

from __future__ import annotations

import json
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from codetrail.repo.mirror import list_tree, read_blobs
from codetrail.repo.rules import ExclusionRules, Reason
from codetrail.repo.secrets import SecretScanner

REGULAR_FILE_MODES = {"100644", "100755"}
# Code points HFS+ ignores in names, so ".g\u200cit" lands as ".git" (git's is_hfs_dotgit checks the same ones).
IGNORED_IN_NAMES = dict.fromkeys([*range(0x200C, 0x2010), *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF])


def on_disk_key(path: str) -> str:
    """The name a case-insensitive, normalizing file system (APFS, HFS+) sees for a path."""
    return unicodedata.normalize("NFC", path.translate(IGNORED_IN_NAMES)).casefold()


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
    parts = path.split("/")
    return all(part and part not in (".", "..") and on_disk_key(part) != ".git" for part in parts)


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
        on_disk: dict[str, list[str]] = {}
        for entry in entries:
            on_disk.setdefault(on_disk_key(entry.path), []).append(entry.path)
        colliding = {path for paths in on_disk.values() if len(paths) > 1 for path in paths}
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
