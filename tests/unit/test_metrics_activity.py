"""The branch's history and its commit types (design section 19.1)."""

from datetime import UTC, date, datetime

from codetrail.config import TargetMetricsSettings
from codetrail.metrics.activity import measure_activity, measure_types
from codetrail.repo.history import WITHHELD_MESSAGE, Commit

DAY = 86_400
START = int(datetime(2025, 1, 6, 12, tzinfo=UTC).timestamp())  # a Monday


def test_activity_counts_months_weeks_and_the_longest_gap() -> None:
    times = [START, START + DAY, START + 15 * DAY, START + 70 * DAY]
    activity = measure_activity(sorted(times, reverse=True), 5, 1, date(2025, 4, 30), months=4)
    assert (activity.first, activity.latest) == (date(2025, 1, 6), date(2025, 3, 17))
    assert activity.months == [("2025-01", 3), ("2025-02", 0), ("2025-03", 1), ("2025-04", 0)]
    assert activity.years == [(2025, 4)]
    assert (activity.active_weeks, activity.weeks) == (3, 11)
    assert activity.longest_gap == (date(2025, 1, 21), date(2025, 3, 17))
    assert (activity.commits, activity.merges, activity.cut) == (5, 1, True)


def test_years_with_no_commit_are_listed() -> None:
    activity = measure_activity([START + 800 * DAY, START], 2, 0, date(2027, 4, 1), months=1)
    assert activity.years == [(2025, 1), (2026, 0), (2027, 1)]
    assert activity.cut is False


def test_one_commit_has_no_gap() -> None:
    activity = measure_activity([START], 1, 0, date(2025, 1, 6), months=2)
    assert (activity.active_weeks, activity.weeks, activity.longest_gap) == (1, 1, None)
    assert activity.months == [("2024-12", 0), ("2025-01", 1)]


def test_commits_on_one_day_have_no_gap() -> None:
    assert measure_activity([START, START + 60], 2, 0, date(2025, 1, 6), months=1).longest_gap is None


def test_no_commits_measure_nothing() -> None:
    activity = measure_activity([], 0, 0, date(2025, 1, 6), months=1)
    assert (activity.first, activity.latest, activity.weeks, activity.years) == (None, None, 0, [])
    assert activity.months == [("2025-01", 0)]


def commit(subject: str) -> Commit:
    return Commit("a" * 40, "", "", subject, "", [], False)


def test_commit_types_count_types_and_scopes() -> None:
    subjects = ["feat(web): a", "Feat(web): b", "fix: c", "docs(docs)!: d", "Add e", WITHHELD_MESSAGE]
    found = measure_types([commit(subject) for subject in subjects], TargetMetricsSettings().types, 20_000)
    assert (found.matched, found.measured, found.used) == (4, 5, True)
    assert found.types == [("feat", 2), ("docs", 1), ("fix", 1)]
    assert found.scopes == [("web", 2), ("docs", 1)]


def test_commit_types_need_half_the_subjects() -> None:
    found = measure_types([commit(s) for s in ["feat: a", "Add b", "Add c"]], TargetMetricsSettings().types, 100)
    assert (found.matched, found.used) == (1, False)
    assert measure_types([], TargetMetricsSettings().types, 100).used is False


def test_only_the_start_of_a_subject_is_matched() -> None:
    assert measure_types([commit("feat: a")], TargetMetricsSettings().types, 3).matched == 0
    assert measure_types([commit("feat: a")], TargetMetricsSettings().types, 6).matched == 1
