"""What the page reads for one target: the sources' manifest, the fact store and the source text.

Source text is served only for paths listed in the manifest's files, which passed the exclusion rules, and only from
`source/`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath

from codetrail.config import Paths
from codetrail.database import connect
from codetrail.facts.store import FactStore
from codetrail.repo.source import SourceManifest

MAX_SOURCE_BYTES = 2_000_000


class TargetView:
    def __init__(self, paths: Paths, name: str) -> None:
        self.name = name
        self.data = paths.target_data(name)

    def manifest(self) -> SourceManifest | None:
        return SourceManifest.load(self.data / "source.json")

    @contextmanager
    def store(self) -> Iterator[FactStore]:
        connection = connect(self.data / "codetrail.db")
        try:
            yield FactStore(connection)
        finally:
            connection.close()

    def areas(self) -> list[tuple[str, int]]:
        """Top-level folders of the visible files, with how many files each holds."""
        manifest = self.manifest()
        if manifest is None:
            return []
        counts = Counter(path.split("/", 1)[0] for path in manifest.files if "/" in path)
        return sorted(counts.items())

    def files_under(self, scope: str) -> list[str]:
        manifest = self.manifest()
        prefix = scope.rstrip("/") + "/"
        return [path for path in manifest.files if path.startswith(prefix)] if manifest else []

    def read_source(self, path: str) -> str | None:
        """The text of a visible file, or None when it isn't visible, isn't text, or is too large to show."""
        manifest = self.manifest()
        if manifest is None or path not in manifest.files:
            return None
        root = (self.data / "source").resolve()
        file = (root / PurePosixPath(path)).resolve()
        if not file.is_relative_to(root) or not file.is_file() or file.stat().st_size > MAX_SOURCE_BYTES:
            return None
        try:
            return file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None
