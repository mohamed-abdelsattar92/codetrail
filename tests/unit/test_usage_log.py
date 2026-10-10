"""Each call's usage is recorded, priced when the provider gives no cost, and plan windows kept (design 15.3)."""

import sqlite3
from pathlib import Path

import pytest

from codetrail.assistant import PlanWindow, Usage
from codetrail.assistant.usage import UsageLog, priced
from codetrail.config import DEFAULT_PRICES, Price
from codetrail.database import connect

PRICES = DEFAULT_PRICES | {"gpt-5.5-codex": Price(input=1.0, output=8.0, cached_input=0.1)}


def test_a_price_covers_input_cached_input_and_output() -> None:
    usage = Usage("codex", "gpt-5.5-codex", 1_000_000, 2_000_000, 500_000)
    assert priced(usage, PRICES) == 1.0 + 0.2 + 4.0
    assert priced(Usage("codex", "unknown-model", 10, 0, 10), PRICES) is None
    assert priced(Usage("local", "qwen3:14b", 10, 0, 10), PRICES) == 0.0


def test_calls_are_recorded_with_their_cost(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    log = UsageLog(connection, PRICES)
    windows = (PlanWindow("five_hour", 0.07, 1791192000), PlanWindow("seven_day", 0.63, 1791216000))
    assert log.record("write", Usage("claude_code", "claude-sonnet-5-5", 100, 0, 10, 0.25, windows)) == 0.25
    assert log.record("write", Usage("codex", "gpt-5.5-codex", 1_000_000, 0, 0)) == 1.0
    assert log.record("answer", Usage("codex", "unknown-model", 10, 0, 10)) == 0.0
    assert log.record("write", Usage()) == 0.0  # an empty usage (a fake) isn't recorded
    rows = connection.execute("SELECT kind, provider, model, cost_usd FROM assistant_calls ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [
        ("write", "claude_code", "claude-sonnet-5-5", 0.25),
        ("write", "codex", "gpt-5.5-codex", 1.0),
        ("answer", "codex", "unknown-model", None),
    ]
    log.record("plan", Usage("claude_code", "claude-opus-5-5", 1, 0, 1, 0.01, (PlanWindow("five_hour", 0.09, 1),)))
    readings = {row["window"]: row["utilization"] for row in connection.execute("SELECT * FROM plan_usage")}
    assert readings == {"five_hour": 0.09, "seven_day": 0.63}


def test_a_call_whose_plan_window_fails_to_save_leaves_nothing_recorded(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    log = UsageLog(connection, PRICES)
    windows = (PlanWindow("five_hour", 0.07, 1791192000), PlanWindow("seven_day", None, 1791216000))  # type: ignore[arg-type]
    with pytest.raises(sqlite3.IntegrityError):
        log.record("write", Usage("claude_code", "claude-sonnet-5-5", 100, 0, 10, 0.25, windows))
    assert not connection.in_transaction
    assert connection.execute("SELECT COUNT(*) FROM assistant_calls").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM plan_usage").fetchone()[0] == 0


def test_a_failure_that_ends_the_transaction_itself_is_raised_as_it_is(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    # SQLite rolls the whole transaction back itself after some errors (a full disk); this trigger does the same.
    connection.execute(
        "CREATE TRIGGER refuse BEFORE INSERT ON plan_usage BEGIN SELECT RAISE(ROLLBACK, 'refused by test'); END"
    )
    usage = Usage("claude_code", "claude-sonnet-5-5", 100, 0, 10, 0.25, (PlanWindow("five_hour", 0.07, 1),))
    with pytest.raises(sqlite3.IntegrityError, match="refused by test"):
        UsageLog(connection, PRICES).record("write", usage)
    assert connection.execute("SELECT COUNT(*) FROM assistant_calls").fetchone()[0] == 0


def test_the_last_update_sums_its_plan_write_and_digest_calls(tmp_path: Path) -> None:
    from codetrail.assistant.usage import last_update_usage

    connection = connect(tmp_path / "codetrail.db")
    assert last_update_usage(connection) is None

    def call(at: str, kind: str, cost: float | None, tokens: int = 1000) -> None:
        connection.execute(
            "INSERT INTO assistant_calls (called_at, kind, provider, model, input_tokens, cached_input_tokens,"
            " output_tokens, cost_usd) VALUES (?, ?, 'claude_code', 'm', ?, 0, 0, ?)",
            (at, kind, tokens, cost),
        )

    def snapshot(at: str) -> None:
        connection.execute("INSERT INTO snapshots (commit_sha, taken_at) VALUES ('c', ?)", (at,))

    snapshot("2026-10-01T09:00:00+00:00")
    call("2026-10-01T09:01:00+00:00", "plan", 1.0)
    snapshot("2026-10-02T09:00:00+00:00")
    call("2026-10-02T09:01:00+00:00", "plan", 0.5)
    call("2026-10-02T09:02:00+00:00", "write", 0.25, tokens=3000)
    call("2026-10-02T09:03:00+00:00", "answer", 9.0)  # a question, not part of the update
    call("2026-10-02T09:04:00+00:00", "digest", None)
    snapshot("2026-10-03T09:00:00+00:00")  # a facts-only update since: the last paid update is still the one above
    usage = last_update_usage(connection)
    assert usage is not None
    assert (usage.tokens, usage.cost_usd, usage.calls) == (5000, 0.75, 3)
    assert usage.finished_at == "2026-10-02T09:04:00+00:00"
