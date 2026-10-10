"""The repository statistics' report: history, releases, code size and facts, read from the target's data (19.3).

Files are read from `source/` through the allowed-files reader, so excluded and secret files are never read; commit
subjects and tags pass gitleaks first. If the history or the tags can't be read, those parts are missing and the rest of
the report stands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pathspec import GitIgnoreSpec

from codetrail.config import GlobalConfig, Paths, load_target
from codetrail.errors import CodetrailError
from codetrail.facts.store import FactStore
from codetrail.guide import GuideRepository, Page
from codetrail.metrics.activity import Activity, CommitTypes, measure_activity, measure_types
from codetrail.metrics.churn import Churn, measure_churn
from codetrail.metrics.coverage import page_scope
from codetrail.metrics.inventory import Inventory, measure_inventory
from codetrail.metrics.rationale import measure_rationale
from codetrail.metrics.releases import Releases, measure_releases
from codetrail.metrics.report import Metric, Value
from codetrail.metrics.size import CodeSize, measure_size
from codetrail.repo.history import (
    changed_files,
    commit_count,
    commit_times,
    commit_totals,
    files_changed_since,
    first_commit_time,
    latest_commits,
    read_tags,
)
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest, allowed_reader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepositoryReport:
    activity: Activity | None  # None when the history couldn't be read
    types: CommitTypes | None  # None, with the releases, when the commits or tags couldn't be read
    releases: Releases | None
    size: CodeSize
    inventory: Inventory
    churn: Churn | None = None  # None when the history couldn't be read

    def values(self) -> dict[Metric, Value]:
        """The statistics recorded per update for their trend (design section 19.4)."""
        size = self.size
        values = {
            Metric.CODE_LINES: Value(size.code_lines, None),
            Metric.SOURCE_FILES: Value(size.files, None),
            Metric.TEST_SHARE: Value(size.test_lines, size.code_lines),
        }
        if self.activity is not None:
            values[Metric.COMMITS] = Value(self.activity.commits, None)
        return {metric: values[metric] for metric in Metric if metric in values}


def build_repository_report(
    paths: Paths, name: str, settings: GlobalConfig, store: FactStore, today: date
) -> RepositoryReport:
    target = load_target(paths, name)
    data = paths.target_data(name)
    manifest = SourceManifest.load(data / "source.json")
    files = manifest.files if manifest else {}
    read = allowed_reader(data / "source", files, settings.extract.max_file_bytes)
    documents = GitIgnoreSpec.from_lines(target.metrics.document_globs)
    tests = GitIgnoreSpec.from_lines(target.metrics.test_globs)
    size = measure_size(files, read, settings.metrics.languages, documents, tests)
    activity = types = releases = churn = None
    if manifest is not None:
        mirror, end = data / "mirror.git", manifest.commit
        try:
            commits, merges = commit_totals(mirror, end)
            times = commit_times(mirror, end, settings.metrics.history_limit)
            activity = measure_activity(times, commits, merges, today, settings.metrics.activity_months)
            first = first_commit_time(mirror, end) if activity.cut else None
            if first is not None:  # the times read stop short of the first commit
                activity = replace(activity, first=datetime.fromtimestamp(first, UTC).date())
        except (CodetrailError, OSError) as error:
            logger.warning("The history isn't available: %s", error)
        try:
            scanner = SecretScanner(settings.tools)
            latest = latest_commits(mirror, end, settings.metrics.commit_window, scanner)
            types = measure_types(latest, target.metrics.types, settings.metrics.max_message_chars)
            tags = read_tags(mirror, end, scanner)
            newest = max(tags, key=lambda tag: (tag.date, tag.name), default=None)
            releases = measure_releases(tags, commit_count(mirror, newest.commit, end) if newest else None)
        except (CodetrailError, OSError) as error:
            types = None
            logger.warning("The commit types and releases aren't available: %s", error)
        try:
            changes = changed_files(mirror, end, settings.metrics.churn_window)
            recent = files_changed_since(mirror, end, today - timedelta(days=settings.metrics.quiet_days))
            code = {file.path for file in size.largest}
            areas = _areas(data / "guide", target.adr.paths, target.metrics.document_globs)
            churn = measure_churn(changes, files, code, recent, areas)
        except (CodetrailError, OSError) as error:
            logger.warning("Where change happens isn't available: %s", error)
    inventory = measure_inventory(store, settings.metrics.trend_updates)
    return RepositoryReport(activity, types, releases, size, inventory, churn)


def _areas(guide_root: Path, adr_paths: list[str], document_globs: list[str]) -> list[tuple[Page, list[str], int, int]]:
    """Each area page with a usable scope, with its documented and inferred rationale blocks."""
    guide = GuideRepository(guide_root)
    pages = guide.pages() if guide.root.exists() else []
    rationale = measure_rationale(pages, GitIgnoreSpec.from_lines(adr_paths), GitIgnoreSpec.from_lines(document_globs))
    counts = {row.page.id: (row.documented, row.inferred) for row in rationale.pages}
    areas = []
    for page in pages:
        entry = page_scope(page) if page.kind == "area" else None
        if entry is not None:
            areas.append((page, entry.scope_paths, *counts.get(page.id, (0, 0))))
    return areas
