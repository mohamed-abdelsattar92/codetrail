"""An update: refresh the sources, extract and record the facts, and write the guide, under the target's lock.

Facts are recorded before the guide is written: they are true whatever happens to the guide, and a page that isn't
rewritten stays affected, because that is derived from its front matter (design section 6.8).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import anyio

from codetrail.assistant import Assistant
from codetrail.assistant.estimate import UpdateEstimate, estimate_update
from codetrail.assistant.routing import build_assistant
from codetrail.assistant.status import require_ready
from codetrail.assistant.usage import UsageLog
from codetrail.config import (
    ExtractSettings,
    Paths,
    TargetConfig,
    check_containment,
    load_global,
    load_target,
    model_choice,
)
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.extract import Extraction, Extractor, run_extractors
from codetrail.extract.adr import AdrExtractor
from codetrail.extract.openapi import OpenApiExtractor
from codetrail.extract.python import PythonExtractor
from codetrail.extract.swift import SwiftExtractor
from codetrail.extract.terraform import TerraformExtractor
from codetrail.extract.typescript import TypeScriptExtractor
from codetrail.facts import FactDiff, Snapshot
from codetrail.facts.store import FactStore
from codetrail.generate.run import GenerationContext, GenerationResult, PlannedWork, generate_guide, planned_work
from codetrail.guide import GuideRepository
from codetrail.learn import LearningState
from codetrail.lock import target_in_use, target_lock
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
    estimate: UpdateEstimate | None = None
    declined: bool = False  # the reader didn't confirm the estimate; facts were refreshed, the guide wasn't touched


def _calls(target: TargetConfig, work: PlannedWork) -> list[tuple[str, str, str, int, int]]:
    """The update's paid calls for its estimate: kind, provider, model, expected count, maximum count."""
    plan, write, digest = (model_choice(getattr(target.models, kind)) for kind in ("plan", "write", "digest"))
    return [
        ("plan", *plan, work.plan_calls, work.plan_calls),
        ("write", *write, work.pages_expected, work.page_calls_max),
        ("digest", *digest, int(work.digest_expected), int(work.digest_possible)),
    ]


def build_extractors(target: TargetConfig, extract: ExtractSettings | None = None) -> list[Extractor]:
    extract = extract or ExtractSettings()
    available: dict[str, Extractor] = {
        "python": PythonExtractor(),
        "adr": AdrExtractor(target.adr.paths),
        "openapi": OpenApiExtractor(target.openapi.paths),
        "terraform": TerraformExtractor(),
        "swift": SwiftExtractor(),
        "typescript": TypeScriptExtractor(extract.max_tsconfig_paths),
    }
    return [available[name] for name in target.extractors]


def run_update(
    paths: Paths,
    name: str,
    claude: Assistant | None = None,
    facts_only: bool = False,
    confirm: Callable[[UpdateEstimate], bool] | None = None,
) -> UpdateResult:
    """Refreshes the sources and facts (free), then, unless `facts_only`, estimates the guide's paid work and asks
    `confirm` before doing it (design section 15.4). An update with a real assistant always needs `confirm`."""
    with target_in_use(paths, name):  # so the target isn't removed during the update
        target = load_target(paths, name)
        check_containment(paths, target.repository)  # before the lock creates the data folder
        settings = load_global(paths)
        sign_ins: dict[str, str] = {}
        if claude is None and not facts_only:  # before any work: the providers this update uses must be ready
            if confirm is None:
                raise CodetrailError("An update that calls an assistant must show its estimate and be confirmed first.")
            statuses = require_ready(target, settings, kinds=("plan", "write", "digest"))
            sign_ins = {status.provider: status.method for status in statuses}
        data = paths.target_data(name)
        with target_lock(paths, name):
            manifest = refresh_while_locked(paths, name)
            source = data / "source"
            extraction = run_extractors(
                source, manifest.files, build_extractors(target, settings.extract), settings.extract.max_file_bytes,
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
                rules = ExclusionRules(
                    ignore_lines(paths, name, read_file_at(mirror, manifest.commit, TARGET_IGNORE_FILE))
                )
                excluded = manifest.excluded_paths()
                context = GenerationContext(
                    target=name,
                    guide=GuideRepository(data / "guide"),
                    store=store,
                    manifest=manifest,
                    source_root=source,
                    mirror=mirror,
                    scanner=SecretScanner(settings.tools),
                    visible=lambda path: rules.reason(path) is None and path not in excluded,
                    max_pages=target.generation.max_pages_per_update,
                    concurrency=target.generation.concurrency,
                    max_budget_usd=target.generation.max_budget_usd_per_update,
                    max_tokens=target.generation.max_tokens_per_update,
                    previous_commit=previous.commit if previous else None,
                    diff=diff,
                    learned=LearningState(connection).learned_page_ids(),
                    usage=UsageLog(connection, settings.prices),
                )
                if confirm is not None:
                    work = planned_work(context)
                    estimate = estimate_update(connection, _calls(target, work), settings.estimates, settings.prices,
                                               sign_ins, target.generation.max_budget_usd_per_update,
                                               target.generation.max_tokens_per_update)  # fmt: skip
                    if not confirm(estimate):
                        return UpdateResult(manifest, snapshot, diff, extraction, None, estimate, declined=True)
                writer = claude or build_assistant(source, settings, target)
                generation = anyio.run(generate_guide, context, writer)
            finally:
                connection.close()
        return UpdateResult(manifest, snapshot, diff, extraction, generation)
