"""Each call's usage is recorded, priced when the provider gives no cost, and plan windows kept (design 15.3)."""

from pathlib import Path

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
