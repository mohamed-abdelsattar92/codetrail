"""Each metric's value per update, in the `metric_values` table, for the trend (design section 18.3)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from codetrail.metrics.report import Metric, Value

KNOWN = {str(metric) for metric in Metric}


def record_values(connection: sqlite3.Connection, snapshot_id: int, values: Mapping[Metric, Value]) -> None:
    """Replaces the snapshot's values with these, in one transaction."""
    connection.execute("BEGIN")
    try:
        connection.execute("DELETE FROM metric_values WHERE snapshot = ?", (snapshot_id,))
        connection.executemany(
            "INSERT INTO metric_values (snapshot, metric, numerator, denominator) VALUES (?, ?, ?, ?)",
            [(snapshot_id, str(metric), value.numerator, value.denominator) for metric, value in values.items()],
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:  # SQLite ends it itself after some errors
            connection.execute("ROLLBACK")
        raise


def recent_values(connection: sqlite3.Connection, metric: Metric, limit: int) -> list[tuple[int, Value]]:
    """The metric's last `limit` values with their snapshots, oldest first."""
    rows = connection.execute(
        "SELECT snapshot, numerator, denominator FROM metric_values WHERE metric = ? ORDER BY snapshot DESC LIMIT ?",
        (str(metric), limit),
    ).fetchall()
    return [(row["snapshot"], Value(row["numerator"], row["denominator"])) for row in reversed(rows)]


def values_before(connection: sqlite3.Connection, snapshot_id: int) -> dict[Metric, Value]:
    """The values of the latest update before the snapshot that recorded any."""
    rows = connection.execute(
        "SELECT metric, numerator, denominator FROM metric_values"
        " WHERE snapshot = (SELECT MAX(snapshot) FROM metric_values WHERE snapshot < ?)",
        (snapshot_id,),
    )
    return {
        Metric(row["metric"]): Value(row["numerator"], row["denominator"]) for row in rows if row["metric"] in KNOWN
    }
