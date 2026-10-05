"""Estimates of what assistant calls will use, before they are made; they never call a provider (design 15.4).

Tokens per call are the median of the last `estimates.history_size` recorded calls of the same kind, provider and
model, once there are three; before that, the starting values in `[estimates]`. The cost is the median recorded cost,
or the tokens at the configured prices; local models cost nothing; a model with no price has tokens only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import median

from codetrail.assistant import Usage
from codetrail.assistant.usage import priced
from codetrail.config import EstimateSettings, Price

MIN_HISTORY = 3


@dataclass(frozen=True)
class CallEstimate:
    kind: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None  # None when the model has no configured price
    from_history: bool

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class PlanUsageReading:
    provider: str
    window: str
    utilization: float
    resets_at: int
    observed_at: str


def estimate_call(
    connection: sqlite3.Connection,
    kind: str,
    provider: str,
    model: str,
    settings: EstimateSettings,
    prices: Mapping[str, Price],
) -> CallEstimate:
    rows = connection.execute(
        "SELECT input_tokens, cached_input_tokens, output_tokens, cost_usd FROM assistant_calls"
        " WHERE kind = ? AND provider = ? AND model = ? ORDER BY id DESC LIMIT ?",
        (kind, provider, model, settings.history_size),
    ).fetchall()
    if len(rows) >= MIN_HISTORY:
        input_tokens = int(median(row["input_tokens"] + row["cached_input_tokens"] for row in rows))
        output_tokens = int(median(row["output_tokens"] for row in rows))
        costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        cost = float(median(costs)) if costs else priced(Usage(provider, model, input_tokens, 0, output_tokens), prices)
        return CallEstimate(kind, provider, model, input_tokens, output_tokens, cost, True)
    guess = getattr(settings, kind)
    cost = priced(Usage(provider, model, guess.input, 0, guess.output), prices)
    return CallEstimate(kind, provider, model, guess.input, guess.output, cost, False)


def plan_usage(connection: sqlite3.Connection) -> list[PlanUsageReading]:
    rows = connection.execute(
        "SELECT provider, window, utilization, resets_at, observed_at FROM plan_usage ORDER BY provider, window"
    ).fetchall()
    return [PlanUsageReading(row["provider"], row["window"], float(row["utilization"]), int(row["resets_at"]),
                             str(row["observed_at"])) for row in rows]  # fmt: skip
