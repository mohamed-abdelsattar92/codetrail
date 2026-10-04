"""An update: refresh the sources, extract the facts and record them, under the target's lock (design section 2.4)."""

from __future__ import annotations

from dataclasses import dataclass

from codetrail.config import Paths, TargetConfig, check_containment, load_global, load_target
from codetrail.database import connect
from codetrail.extract import Extraction, Extractor, run_extractors
from codetrail.extract.adr import AdrExtractor
from codetrail.extract.python import PythonExtractor
from codetrail.facts import FactDiff, Snapshot
from codetrail.facts.store import FactStore
from codetrail.lock import target_lock
from codetrail.repo.refresh import refresh_while_locked
from codetrail.repo.source import SourceManifest


@dataclass(frozen=True)
class UpdateResult:
    manifest: SourceManifest
    snapshot: Snapshot
    diff: FactDiff
    extraction: Extraction


def build_extractors(target: TargetConfig) -> list[Extractor]:
    available: dict[str, Extractor] = {"python": PythonExtractor(), "adr": AdrExtractor(target.adr.paths)}
    return [available[name] for name in target.extractors]


def run_update(paths: Paths, name: str) -> UpdateResult:
    target = load_target(paths, name)
    check_containment(paths, target.repository)  # before the lock creates the data folder
    limit = load_global(paths).extract.max_file_bytes
    with target_lock(paths, name):
        manifest = refresh_while_locked(paths, name)
        source = paths.target_data(name) / "source"
        extraction = run_extractors(source, manifest.files, build_extractors(target), limit)
        store = FactStore(connect(paths.target_data(name) / "codetrail.db"))
        try:
            snapshot, diff = store.record(manifest.commit, extraction.entities, extraction.relations)
        finally:
            store.connection.close()
    return UpdateResult(manifest, snapshot, diff, extraction)
