"""The documentation metrics' report over a real target, and its recording at each update (design 18.2, 18.3)."""

import logging
from datetime import date
from pathlib import Path

import pytest

from codetrail.assistant import PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.database import connect
from codetrail.facts.store import FactStore
from codetrail.metrics.report import Metric, Value, build_report
from codetrail.update import run_update
from tests.fixtures.repos import Commit, add_commit, make_repository

FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi", "httpx"]\n',
    "app/main.py": "from app import db\n",
    "app/db.py": "",
    "README.md": "# Shop\n\nBuilt with FastAPI.\n",
    "docs/adr/0001-x.md": "# 0001. X\n\nStatus: accepted\n\nWe chose Python.\n",
}
PLAN = PlanDraft([{"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []}])


def page_writer(request: PageRequest) -> PageDraft:
    body = '> [!documented] docs/adr/0001-x.md#L5-L5\n> "We chose Python."\n\n> [!inferred]\n> It reads well.\n'
    checks = [{"id": "q", "question": "Why?", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}]
    return PageDraft(body, checks, ["app/main.py"])


def writer() -> FakeAssistant:
    return FakeAssistant(plans=[PLAN], page_writer=page_writer)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "target", [FILES])
    add_commit(checkout, {"app/db.py": "x = 1\n"}, "feat: b\n\nWhy: it helps.")
    add_commit(checkout, {"app/db.py": "x = 2\n"}, "fix: c\n\nJust a body.")
    write_target(paths, "shop", checkout, "develop")
    return paths


def rows(paths: Paths) -> dict[int, dict[str, tuple[int, int | None]]]:
    connection = connect(paths.target_data("shop") / "codetrail.db")
    try:
        found: dict[int, dict[str, tuple[int, int | None]]] = {}
        for row in connection.execute("SELECT * FROM metric_values"):
            found.setdefault(row["snapshot"], {})[row["metric"]] = (row["numerator"], row["denominator"])
        return found
    finally:
        connection.close()


def test_the_report_measures_the_guide_the_history_and_the_facts(paths: Paths) -> None:
    run_update(paths, "shop", claude=writer())
    connection = connect(paths.target_data("shop") / "codetrail.db")
    try:
        store = FactStore(connection)
        report = build_report(paths, "shop", GlobalConfig(), store, date(2026, 10, 10))
        total = len(store.entities())
    finally:
        connection.close()
    assert report.rationale.by_source["adr"] == 1
    assert [block.first_line for block in report.rationale.inferred_blocks] == ["It reads well."]
    assert report.commits is not None
    assert [commit.subject for commit in report.commits.without_why] == ["fix: c", "Commit 0"]
    assert report.decisions.uncited == []  # the page quotes the ADR
    assert [entity.id for entity in report.mentions.unmentioned] == ["package:pypi/httpx"]
    values = report.values()
    assert values[Metric.DOCUMENTED_SHARE] == Value(1, 2)
    assert values[Metric.COMMIT_WHY_SHARE] == Value(1, 3)
    assert values[Metric.ADR_ATTENTION] == Value(0, None)
    assert values[Metric.MENTIONED_SHARE] == Value(2, 3)  # fastapi in the README, the root project by its README
    assert values[Metric.EXPLAINED_SHARE] == Value(report.explained.explained, total)
    assert {entity.id for entity in report.explained.unexplained}.isdisjoint({"module:app/db.py", "module:app/main.py"})


def test_every_kind_of_finished_update_records_the_metrics(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    run_update(paths, "shop", claude=writer(), confirm=lambda estimate: False)  # declined
    run_update(paths, "shop", claude=writer())
    recorded = rows(paths)
    assert sorted(recorded) == [1, 2, 3]
    assert all(set(found) == {str(metric) for metric in Metric} for found in recorded.values())
    assert recorded[1][str(Metric.DOCUMENTED_SHARE)] == (0, 0)
    assert recorded[3][str(Metric.DOCUMENTED_SHARE)] == (1, 2)


def test_a_failed_update_records_nothing(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*arguments: object) -> None:
        raise RuntimeError("the writing failed")

    monkeypatch.setattr("codetrail.update.generate_guide", fail)
    with pytest.raises(RuntimeError):
        run_update(paths, "shop", claude=writer())
    assert rows(paths) == {}


def test_metrics_that_cant_be_computed_only_warn(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(*arguments: object) -> None:
        raise ValueError("unexpected")

    monkeypatch.setattr("codetrail.update.build_report", fail)
    with caplog.at_level(logging.WARNING):
        result = run_update(paths, "shop", facts_only=True)
    assert result.snapshot.id == 1
    assert str(Metric.DOCUMENTED_SHARE) not in rows(paths)[1]  # the repository statistics are still recorded
    assert "The documentation metrics weren't recorded" in caplog.text
