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


@dataclass(frozen=True)
class EstimateLine:
    call: CallEstimate
    expected: int  # how many calls are expected
    maximum: int  # how many at most


@dataclass(frozen=True)
class UpdateEstimate:
    """What an update's paid calls are expected to use, and at most, before it starts (design section 15.4)."""

    lines: list[EstimateLine]
    sign_ins: dict[str, str]  # provider -> how it is signed in
    plan_usage: list[PlanUsageReading]
    budget_usd: float  # the update's own limit (generation.max_budget_usd_per_update)

    @property
    def expected_tokens(self) -> int:
        return sum(line.call.tokens * line.expected for line in self.lines)

    @property
    def maximum_tokens(self) -> int:
        return sum(line.call.tokens * line.maximum for line in self.lines)

    @property
    def expected_usd(self) -> float | None:
        return _dollars(self.lines, expected=True)

    @property
    def maximum_usd(self) -> float | None:
        total = _dollars(self.lines, expected=False)
        return None if total is None else min(total, self.budget_usd)

    def as_json(self) -> dict[str, object]:
        return {
            "lines": [{"kind": line.call.kind, "provider": line.call.provider, "model": line.call.model,
                       "expected": line.expected, "maximum": line.maximum, "tokens_each": line.call.tokens,
                       "cost_each_usd": line.call.cost_usd, "from_history": line.call.from_history}
                      for line in self.lines],
            "expected_tokens": self.expected_tokens, "maximum_tokens": self.maximum_tokens,
            "expected_usd": self.expected_usd, "maximum_usd": self.maximum_usd, "budget_usd": self.budget_usd,
            "sign_ins": self.sign_ins,
            "plan_usage": [{"provider": reading.provider, "window": reading.window,
                            "utilization": reading.utilization, "resets_at": reading.resets_at,
                            "observed_at": reading.observed_at} for reading in self.plan_usage],
        }  # fmt: skip


def estimate_update(
    connection: sqlite3.Connection,
    calls: list[tuple[str, str, str, int, int]],  # kind, provider, model, expected, maximum
    settings: EstimateSettings,
    prices: Mapping[str, Price],
    sign_ins: Mapping[str, str],
    budget_usd: float,
) -> UpdateEstimate:
    lines = [
        EstimateLine(estimate_call(connection, kind, provider, model, settings, prices), expected, maximum)
        for kind, provider, model, expected, maximum in calls
        if maximum > 0
    ]
    return UpdateEstimate(lines, dict(sign_ins), plan_usage(connection), budget_usd)


def _dollars(lines: list[EstimateLine], expected: bool) -> float | None:
    total = 0.0
    for line in lines:
        count = line.expected if expected else line.maximum
        if count and line.call.cost_usd is None:
            return None
        total += (line.call.cost_usd or 0.0) * count
    return total


def tokens_text(tokens: int) -> str:
    """A token count for people: 950, 12k, 1.4M."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{round(tokens / 1_000)}k"
    return str(tokens)


def describe(estimate: UpdateEstimate) -> list[str]:
    """The estimate as lines of text, for the command line."""
    lines = ["This update will call:"]
    for line in estimate.lines:
        count = str(line.expected) if line.expected == line.maximum else f"{line.expected} to {line.maximum}"
        cost = "free" if line.call.cost_usd == 0 else (
            "no price configured" if line.call.cost_usd is None else f"~${line.call.cost_usd:.2f} each")  # fmt: skip
        source = "" if line.call.from_history else " (a starting guess)"
        lines.append(f"  {line.call.kind:<7} {line.call.provider} · {line.call.model or 'default model'}: "
                     f"{count} call(s), ~{tokens_text(line.call.tokens)} tokens each{source}, {cost}")  # fmt: skip
    expected, maximum = estimate.expected_usd, estimate.maximum_usd
    money = "" if expected is None or maximum is None else f", ~${expected:.2f} (at most ${maximum:.2f})"
    lines.append(f"Expected: ~{tokens_text(estimate.expected_tokens)} tokens (at most "
                 f"~{tokens_text(estimate.maximum_tokens)}){money} at API prices. "
                 f"The update stops at its budget of ${estimate.budget_usd:.2f}.")  # fmt: skip
    for provider, method in estimate.sign_ins.items():
        if "subscription" in method.lower():
            lines.append(f"{provider}: {method}, so no charge; the work counts against your plan's usage limits.")
        elif provider != "local":
            lines.append(f"{provider}: {method}, billed to that account at its prices.")
    for reading in estimate.plan_usage:
        lines.append(f"Plan usage ({reading.provider}, {reading.window.replace('_', ' ')}): "
                     f"{reading.utilization:.0%}, as of {reading.observed_at} UTC.")  # fmt: skip
    return lines
