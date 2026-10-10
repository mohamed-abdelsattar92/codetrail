"""The repository statistics' report over a real target, and its recording at each update (design 19.3, 19.4)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from codetrail.config import GlobalConfig, MetricsSettings, Paths, write_target
from codetrail.database import connect
from codetrail.facts.store import FactStore
from codetrail.metrics.report import Metric, Value
from codetrail.metrics.repository import build_repository_report
from codetrail.update import run_update
from tests.fixtures.repos import Commit, add_commit, git, make_repository, write_commit

FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi", "httpx"]\n',
    "app/main.py": "from app import db\nprint(db)\n",
    "app/db.py": "",
    "tests/test_main.py": "def test_x():\n    pass\n",
    "README.md": "# Shop\n",
    ".env": "SECRET=1\n",
}


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "target", [FILES])
    git(checkout, "tag", "-a", "v1", "-m", "Release one", date=1)
    add_commit(checkout, {"app/db.py": "x = 1\n"}, "feat(app): b\n\nWhy: it helps.")
    add_commit(checkout, {"app/db.py": "x = 2\n"}, "fix: c")
    write_target(paths, "shop", checkout, "develop")
    return paths


@contextmanager
def store_of(paths: Paths) -> Iterator[FactStore]:
    connection = connect(paths.target_data("shop") / "codetrail.db")
    try:
        yield FactStore(connection)
    finally:
        connection.close()


def rows(paths: Paths) -> dict[int, dict[str, tuple[int, int | None]]]:
    with store_of(paths) as store:
        found: dict[int, dict[str, tuple[int, int | None]]] = {}
        for row in store.connection.execute("SELECT * FROM metric_values"):
            found.setdefault(row["snapshot"], {})[row["metric"]] = (row["numerator"], row["denominator"])
        return found


def test_the_report_reads_history_tags_files_and_facts(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    with store_of(paths) as store:
        report = build_repository_report(paths, "shop", GlobalConfig(), store, date(2026, 10, 10))
    assert report.activity is not None
    assert (report.activity.commits, report.activity.merges, report.activity.cut) == (3, 0, False)
    assert report.types is not None and report.types.types == [("feat", 1), ("fix", 1)]
    assert report.types.scopes == [("app", 1)]
    assert report.releases is not None and [tag.name for tag in report.releases.tags] == ["v1"]
    assert (report.releases.tags[0].message, report.releases.since_latest) == ("Release one", 2)
    assert [(size.language, size.files, size.lines) for size in report.size.languages] == [
        ("Python", 3, 5),
        ("TOML", 1, 3),
    ]
    assert (report.size.test_lines, report.size.documents.files) == (2, 1)
    assert all(".env" not in file.path for file in report.size.largest)
    assert report.inventory.since is not None
    values = report.values()
    assert values[Metric.COMMITS] == Value(3, None)
    assert values[Metric.CODE_LINES] == Value(8, None)
    assert values[Metric.SOURCE_FILES] == Value(report.size.files, None)
    assert values[Metric.TEST_SHARE] == Value(2, 8)


def test_the_history_can_be_missing_and_the_rest_stands(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    run_update(paths, "shop", facts_only=True)

    def fail(*arguments: object) -> None:
        raise OSError("gone")

    monkeypatch.setattr("codetrail.metrics.repository.commit_totals", fail)
    monkeypatch.setattr("codetrail.metrics.repository.read_tags", fail)
    with store_of(paths) as store:
        report = build_repository_report(paths, "shop", GlobalConfig(), store, date(2026, 10, 10))
    assert (report.activity, report.types, report.releases) == (None, None, None)
    assert Metric.COMMITS not in report.values() and Metric.CODE_LINES in report.values()


def test_an_update_records_both_reports(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    recorded = rows(paths)[1]
    assert set(recorded) == {str(metric) for metric in Metric}
    assert recorded[str(Metric.COMMITS)] == (3, None)


def test_a_failing_repository_report_still_records_the_documentation_metrics(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(*arguments: object) -> None:
        raise ValueError("unexpected")

    monkeypatch.setattr("codetrail.update.build_repository_report", fail)
    with caplog.at_level(logging.WARNING):
        run_update(paths, "shop", facts_only=True)
    recorded = rows(paths)[1]
    assert str(Metric.DOCUMENTED_SHARE) in recorded and str(Metric.COMMITS) not in recorded
    assert "The repository statistics weren't recorded" in caplog.text


def test_a_cut_history_keeps_exact_counts_and_the_real_first_commit(paths: Paths, tmp_path: Path) -> None:
    write_commit(tmp_path / "target", {"app/db.py": "x = 3\n"}, "fix: later", date=60 * 24 * 40)  # 40 days on
    run_update(paths, "shop", facts_only=True)
    settings = GlobalConfig(metrics=MetricsSettings(history_limit=1))
    with store_of(paths) as store:
        report = build_repository_report(paths, "shop", settings, store, date(2026, 10, 10))
    assert report.activity is not None
    assert (report.activity.commits, report.activity.cut) == (4, True)
    assert report.activity.first == date(2026, 9, 21)  # the first commit, not the oldest of the times read
    assert sum(count for _, count in report.activity.years) == 1
