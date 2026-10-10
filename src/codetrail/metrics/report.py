"""The documentation metrics' report: every metric, read from the target's data (design section 18.2).

Documents are read from `source/` through the allowed-files reader, so excluded and secret files are never read; commits
come from the filtered history, with flagged messages withheld. If the history can't be read, the commit metric is
missing and the rest of the report stands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pathspec import GitIgnoreSpec

from codetrail.config import GlobalConfig, Paths, load_target
from codetrail.errors import CodetrailError
from codetrail.facts import EntityKind
from codetrail.facts.store import FactStore
from codetrail.guide import GuideRepository
from codetrail.metrics.commits import CommitMetric, measure_commits
from codetrail.metrics.coverage import ExplainedMetric, MentionMetric, measure_explained, measure_mentions
from codetrail.metrics.decisions import DecisionMetric, measure_decisions
from codetrail.metrics.rationale import RationaleMetric, measure_rationale
from codetrail.repo.history import latest_commits
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest, allowed_reader

logger = logging.getLogger(__name__)


class Metric(StrEnum):
    DOCUMENTED_SHARE = "documented_share"
    COMMIT_WHY_SHARE = "commit_why_share"
    ADR_ATTENTION = "adr_attention"
    MENTIONED_SHARE = "mentioned_share"
    EXPLAINED_SHARE = "explained_share"


@dataclass(frozen=True)
class Value:
    numerator: int
    denominator: int | None  # None for a count

    @property
    def share(self) -> float | None:
        """The share, or None for a count or when there was nothing to measure."""
        return self.numerator / self.denominator if self.denominator else None


@dataclass(frozen=True)
class MetricsReport:
    rationale: RationaleMetric
    commits: CommitMetric | None  # None when the history couldn't be read
    decisions: DecisionMetric
    mentions: MentionMetric
    explained: ExplainedMetric

    def values(self) -> dict[Metric, Value]:
        rationale, mentions = self.rationale, self.mentions
        values = {
            Metric.DOCUMENTED_SHARE: Value(rationale.documented, rationale.documented + rationale.inferred),
            Metric.ADR_ATTENTION: Value(self.decisions.attention, None),
            Metric.MENTIONED_SHARE: Value(len(mentions.mentioned), len(mentions.mentioned) + len(mentions.unmentioned)),
            Metric.EXPLAINED_SHARE: Value(self.explained.explained, self.explained.total),
        }
        if self.commits is not None:
            values[Metric.COMMIT_WHY_SHARE] = Value(self.commits.explains_why, self.commits.measured)
        return {metric: values[metric] for metric in Metric if metric in values}


def build_report(paths: Paths, name: str, settings: GlobalConfig, store: FactStore, today: date) -> MetricsReport:
    target = load_target(paths, name)
    data = paths.target_data(name)
    guide = GuideRepository(data / "guide")
    pages = guide.pages() if guide.root.exists() else []
    manifest = SourceManifest.load(data / "source.json")
    files = manifest.files if manifest else {}
    document_globs = GitIgnoreSpec.from_lines(target.metrics.document_globs)
    read = allowed_reader(data / "source", files, settings.extract.max_file_bytes)
    documents = {}
    for path in files:
        if document_globs.match_file(path):
            content = read(path)
            if content is not None:
                documents[path] = content.decode("utf-8", "replace")
    commits = None
    if manifest is not None:
        try:
            found = latest_commits(data / "mirror.git", manifest.commit, settings.metrics.commit_window,
                                   SecretScanner(settings.tools))  # fmt: skip
            commits = measure_commits(found, target.metrics.why, settings.metrics.max_message_chars)
        except (CodetrailError, OSError) as error:
            logger.warning("The commit metric isn't available: %s", error)
    entities = store.entities()
    decisions = [entity for entity in entities if entity.kind == EntityKind.DECISION]
    return MetricsReport(
        measure_rationale(pages, GitIgnoreSpec.from_lines(target.adr.paths), document_globs),
        commits,
        measure_decisions(decisions, pages, today, settings.metrics.proposed_adr_days),
        measure_mentions(entities, documents),
        measure_explained(entities, pages),
    )
