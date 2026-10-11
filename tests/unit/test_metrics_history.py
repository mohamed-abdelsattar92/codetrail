"""Each metric's value per update, for the trend (design section 18.3)."""

import sqlite3
from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts.store import FactStore
from codetrail.metrics.history import recent_values, record_values, values_before
from codetrail.metrics.report import Metric, Value


def test_values_are_kept_per_snapshot_and_read_back_in_order(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    store = FactStore(connection)
    first, _ = store.record("a" * 40, [], [])
    second, _ = store.record("b" * 40, [], [])
    record_values(connection, first.id, {Metric.DOCUMENTED_SHARE: Value(1, 4), Metric.ADR_ATTENTION: Value(3, None)})
    record_values(connection, second.id, {Metric.DOCUMENTED_SHARE: Value(2, 4)})
    assert recent_values(connection, Metric.DOCUMENTED_SHARE, 12) == [(first.id, Value(1, 4)), (second.id, Value(2, 4))]
    assert recent_values(connection, Metric.DOCUMENTED_SHARE, 1) == [(second.id, Value(2, 4))]
    assert values_before(connection, second.id) == {Metric.DOCUMENTED_SHARE: Value(1, 4),
                                                   Metric.ADR_ATTENTION: Value(3, None)}  # fmt: skip
    assert values_before(connection, first.id) == {}
    record_values(connection, second.id, {Metric.DOCUMENTED_SHARE: Value(3, 4)})  # recorded again: replaced
    assert recent_values(connection, Metric.DOCUMENTED_SHARE, 1) == [(second.id, Value(3, 4))]


def test_a_metric_this_version_doesnt_know_is_ignored(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    first, _ = FactStore(connection).record("a" * 40, [], [])
    second, _ = FactStore(connection).record("b" * 40, [], [])
    connection.execute("INSERT INTO metric_values VALUES (?, 'from_a_newer_version', 1, 2)", (first.id,))
    assert values_before(connection, second.id) == {}


def test_a_share_needs_a_denominator() -> None:
    assert Value(1, 4).share == 0.25
    assert Value(0, 0).share is None
    assert Value(3, None).share is None


def test_a_failure_that_ends_the_transaction_itself_is_raised_as_it_is(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    first, _ = FactStore(connection).record("a" * 40, [], [])
    # SQLite rolls the whole transaction back itself after some errors (a full disk); this trigger does the same.
    connection.execute(
        "CREATE TRIGGER refuse BEFORE INSERT ON metric_values BEGIN SELECT RAISE(ROLLBACK, 'refused by test'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="refused by test"):
        record_values(connection, first.id, {Metric.DOCUMENTED_SHARE: Value(1, 4)})
    assert not connection.in_transaction
