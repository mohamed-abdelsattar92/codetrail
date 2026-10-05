"""An update: refresh the sources, extract and record the facts, and write the guide, under the target's lock.

Facts are recorded before the guide is written: they are true whatever happens to the guide, and a page that isn't
rewritten stays affected, because that is derived from its front matter (design section 6.8).
"""

from __future__ import annotations

from dataclasses import dataclass

import anyio

from codetrail.assistant import Assistant
from codetrail.assistant.routing import build_assistant
from codetrail.assistant.status import require_ready
from codetrail.config import Paths, TargetConfig, check_containment, load_global, load_target
from codetrail.database import connect
from codetrail.extract import Extraction, Extractor, run_extractors
from codetrail.extract.adr import AdrExtractor
from codetrail.extract.openapi import OpenApiExtractor
from codetrail.extract.python import PythonExtractor
from codetrail.extract.swift import SwiftExtractor
from codetrail.extract.terraform import TerraformExtractor
from codetrail.facts import FactDiff, Snapshot
from codetrail.facts.store import FactStore
from codetrail.generate.run import GenerationContext, GenerationResult, generate_guide
from codetrail.guide import GuideRepository
from codetrail.learn import LearningState
from codetrail.lock import target_lock
from codetrail.repo.mirror import read_file_at
from codetrail.repo.refresh import TARGET_IGNORE_FILE, ignore_lines, refresh_while_locked
from codetrail.repo.rules import ExclusionRules
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest


@dataclass(frozen=True)
class UpdateResult:
    manifest: SourceManifest
    snapshot: Snapshot
    diff: FactDiff
    extraction: Extraction
    generation: GenerationResult | None = None


def build_extractors(target: TargetConfig) -> list[Extractor]:
    available: dict[str, Extractor] = {
        "python": PythonExtractor(),
        "adr": AdrExtractor(target.adr.paths),
        "openapi": OpenApiExtractor(target.openapi.paths),
        "terraform": TerraformExtractor(),
        "swift": SwiftExtractor(),
    }
    return [available[name] for name in target.extractors]


def run_update(paths: Paths, name: str, claude: Assistant | None = None, facts_only: bool = False) -> UpdateResult:
    target = load_target(paths, name)
    check_containment(paths, target.repository)  # before the lock creates the data folder
    settings = load_global(paths)
    if claude is None and not facts_only:  # before any work: the providers this update uses must be ready
        require_ready(target, settings, kinds=("plan", "write", "digest"))
    data = paths.target_data(name)
    with target_lock(paths, name):
        manifest = refresh_while_locked(paths, name)
        source = data / "source"
        extraction = run_extractors(
            source, manifest.files, build_extractors(target), settings.extract.max_file_bytes,
            settings.extract.max_attribute_chars,
        )  # fmt: skip
        connection = connect(data / "codetrail.db")
        try:
            store = FactStore(connection)
            snapshot, diff = store.record(manifest.commit, extraction.entities, extraction.relations)
            if facts_only:
                return UpdateResult(manifest, snapshot, diff, extraction)
            previous = store.previous_snapshot(snapshot)
            mirror = data / "mirror.git"
            rules = ExclusionRules(ignore_lines(paths, name, read_file_at(mirror, manifest.commit, TARGET_IGNORE_FILE)))
            excluded = manifest.excluded_paths()
            context = GenerationContext(
                target=name,
                guide=GuideRepository(data / "guide"),
                store=store,
                manifest=manifest,
                source_root=source,
                mirror=mirror,
                scanner=SecretScanner(settings.tools.gitleaks),
                visible=lambda path: rules.reason(path) is None and path not in excluded,
                max_pages=target.generation.max_pages_per_update,
                concurrency=target.generation.concurrency,
                max_budget_usd=target.generation.max_budget_usd_per_update,
                previous_commit=previous.commit if previous else None,
                diff=diff,
                learned=LearningState(connection).learned_page_ids(),
            )
            writer = claude or build_assistant(source, settings, target)
            generation = anyio.run(generate_guide, context, writer)
        finally:
            connection.close()
    return UpdateResult(manifest, snapshot, diff, extraction, generation)
