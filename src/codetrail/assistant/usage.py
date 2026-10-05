"""Records what each assistant call used, and keeps the latest plan usage readings (design section 15.3).

A call's cost is the provider's own figure when it gives one, otherwise its tokens at the configured prices; a local
model costs nothing; a model with no configured price has tokens only. These records are the history the estimates
are made from (section 15.4).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime

from codetrail.assistant import Usage
from codetrail.config import Price

PER_MILLION = 1_000_000


def priced(usage: Usage, prices: Mapping[str, Price]) -> float | None:
    """The usage at the configured prices; zero for local models; None for a model without a price."""
    if usage.provider == "local":
        return 0.0
    price = prices.get(usage.model)
    if price is None:
        return None
    cached = price.cached_input if price.cached_input is not None else price.input
    return (
        usage.input_tokens * price.input + usage.cached_input_tokens * cached + usage.output_tokens * price.output
    ) / PER_MILLION


class UsageLog:
    def __init__(self, connection: sqlite3.Connection, prices: Mapping[str, Price]) -> None:
        self.connection = connection
        self.prices = prices

    def record(self, kind: str, usage: Usage) -> float:
        """Records the call and returns its cost in dollars (zero when unknown) for the update's budget."""
        if not usage.provider:
            return 0.0
        cost = usage.cost_usd if usage.cost_usd is not None else priced(usage, self.prices)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.connection:
            self.connection.execute(
                "INSERT INTO assistant_calls (called_at, kind, provider, model, input_tokens, cached_input_tokens,"
                " output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now, kind, usage.provider, usage.model, usage.input_tokens, usage.cached_input_tokens,
                 usage.output_tokens, cost),
            )  # fmt: skip
            for window in usage.plan_windows:
                self.connection.execute(
                    "INSERT INTO plan_usage (provider, window, utilization, resets_at, observed_at)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT (provider, window) DO UPDATE SET"
                    " utilization = excluded.utilization, resets_at = excluded.resets_at,"
                    " observed_at = excluded.observed_at",
                    (usage.provider, window.window, window.utilization, window.resets_at, now),
                )
        return cost or 0.0
