"""Commits that explain why (design section 18.1)."""

import re
import time

from codetrail.config import TargetMetricsSettings
from codetrail.metrics.commits import measure_commits
from codetrail.repo.history import WITHHELD_MESSAGE, Commit

WHY = re.compile(r"(?im)^\s*(?:#+\s*)?why\b")


def commit(sha: str, subject: str, body: str = "") -> Commit:
    return Commit(sha, "Someone", "2026-10-10T00:00:00Z", subject, body, [], False)


def test_each_commit_is_sorted_into_one_level() -> None:
    commits = [
        commit("a1", "feat: a", "What: a\n\nWhy: it helps"),
        commit("b2", "fix: b", "Just a body."),
        commit("c3", "chore: c"),
        commit("d4", WITHHELD_MESSAGE),
        commit("e5", "docs: e", "## Why\nBecause."),
    ]
    metric = measure_commits(commits, WHY)
    assert (metric.explains_why, metric.body_only, metric.subject_only, metric.withheld) == (2, 1, 1, 1)
    assert metric.measured == 4
    assert [found.sha for found in metric.without_why] == ["b2", "c3"]


def test_a_hostile_body_is_measured_in_linear_time() -> None:
    """A long run of blank lines would make a pattern whose whitespace crosses lines take quadratic time."""
    why = TargetMetricsSettings().why
    hostile = commit("f6", "chore: f", "x" + "\n" * 200_000 + "x")
    started = time.process_time()
    metric = measure_commits([hostile], why, max_chars=1_000_000)
    assert time.process_time() - started < 1
    assert metric.body_only == 1


def test_only_the_start_of_a_long_body_is_matched() -> None:
    late = commit("g7", "feat: g", "x" * 100 + "\nWhy: at the end.")
    assert measure_commits([late], WHY, max_chars=50).body_only == 1
    assert measure_commits([late], WHY, max_chars=1000).explains_why == 1


def test_no_commits_measure_nothing() -> None:
    metric = measure_commits([], WHY)
    assert (metric.measured, metric.withheld, metric.without_why) == (0, 0, [])
