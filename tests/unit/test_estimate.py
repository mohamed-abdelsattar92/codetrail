"""Estimates per call: from Codetrail's own history once there is enough, otherwise starting values (design 15.4)."""

import sqlite3
from pathlib import Path

import pytest

from codetrail.assistant import PlanWindow, Usage
from codetrail.assistant.estimate import estimate_call, plan_usage
from codetrail.assistant.usage import UsageLog
from codetrail.config import DEFAULT_PRICES, EstimateSettings, TokenGuess
from codetrail.database import connect

SETTINGS = EstimateSettings(history_size=4, write=TokenGuess(input=120_000, output=6_000))


@pytest.fixture
def connection(tmp_path: Path) -> sqlite3.Connection:
    return connect(tmp_path / "codetrail.db")


def test_starting_values_before_there_is_history(connection: sqlite3.Connection) -> None:
    estimate = estimate_call(connection, "write", "claude_code", "claude-sonnet-5-5", SETTINGS, DEFAULT_PRICES)
    assert (estimate.input_tokens, estimate.output_tokens) == (120_000, 6_000)
    assert estimate.cost_usd == pytest.approx((120_000 * 2.0 + 6_000 * 10.0) / 1_000_000)
    assert not estimate.from_history


def test_the_median_of_recent_calls_once_there_are_three(connection: sqlite3.Connection) -> None:
    log = UsageLog(connection, DEFAULT_PRICES)
    for tokens in (1_000, 9_000, 3_000, 5_000, 4_000):
        log.record("write", Usage("claude_code", "claude-sonnet-5-5", tokens, 0, tokens // 10, tokens / 1_000_000))
    log.record("write", Usage("claude_code", "claude-opus-5-5", 999_000, 0, 1, 1.0))  # another model
    log.record("answer", Usage("claude_code", "claude-sonnet-5-5", 999_000, 0, 1, 1.0))  # another kind
    estimate = estimate_call(connection, "write", "claude_code", "claude-sonnet-5-5", SETTINGS, DEFAULT_PRICES)
    assert estimate.from_history
    assert estimate.input_tokens == 4_500  # the median of the last 4: 9000, 3000, 5000, 4000
    assert estimate.cost_usd == pytest.approx(0.0045)


def test_two_calls_arent_enough_history(connection: sqlite3.Connection) -> None:
    log = UsageLog(connection, DEFAULT_PRICES)
    for _ in range(2):
        log.record("write", Usage("claude_code", "claude-sonnet-5-5", 10, 0, 1, 0.0))
    assert not estimate_call(
        connection, "write", "claude_code", "claude-sonnet-5-5", SETTINGS, DEFAULT_PRICES
    ).from_history


def test_local_models_are_free_and_unpriced_models_have_no_cost(connection: sqlite3.Connection) -> None:
    assert estimate_call(connection, "answer", "local", "qwen3:14b", SETTINGS, DEFAULT_PRICES).cost_usd == 0.0
    assert estimate_call(connection, "answer", "codex", "gpt-5.5-codex", SETTINGS, DEFAULT_PRICES).cost_usd is None


def test_plan_usage_readings_carry_their_age(connection: sqlite3.Connection) -> None:
    UsageLog(connection, DEFAULT_PRICES).record(
        "plan", Usage("claude_code", "claude-opus-5-5", 1, 0, 1, 0.0, (PlanWindow("five_hour", 0.4, 1791192000),))
    )
    (reading,) = plan_usage(connection)
    assert (reading.provider, reading.window, reading.utilization) == ("claude_code", "five_hour", 0.4)
    assert reading.observed_at


def test_times_read_as_minutes_in_utc() -> None:
    from codetrail.assistant.estimate import PlanUsageReading, UpdateEstimate, describe, when_text

    assert when_text("2026-10-05T06:38:11+00:00") == "2026-10-05 06:38 UTC"
    reading = PlanUsageReading("claude_code", "five_hour", 0.6, 0, "2026-10-05T06:38:11+00:00")
    lines = describe(UpdateEstimate([], {}, [reading], 10.0, 5_000_000))
    assert "five hour): 60%, as of 2026-10-05 06:38 UTC." in lines[-1]


def test_the_estimate_names_each_page_with_why_and_the_skipped_ones() -> None:
    from codetrail.assistant.estimate import PageToWrite, UpdateEstimate, describe

    pages = [PageToWrite("The API", "update", 2), PageToWrite("Payments", "catching_up", 0, revise=True),
             PageToWrite("The ledger", "new", 3), PageToWrite("Retries", "outline", 0)]  # fmt: skip
    lines = describe(UpdateEstimate([], {}, [], 10.0, 5_000_000, pages, ["The root"]))
    assert "Pages, in the order they're written:" in lines
    assert "  The API: 2 of its facts changed in this update" in lines
    assert "  Payments (revised): catching up, since its facts changed after it was written" in lines
    assert "  The ledger: a new page" in lines
    assert "  Retries: its outline entry changed" in lines
    assert "Skipped, since they failed last time and nothing in them changed: The root" in lines
