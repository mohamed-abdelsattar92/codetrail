"""The Documentation page's tiles, and the report kept in memory between updates (design section 18.4)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from codetrail.metrics.report import Metric, Value
from codetrail.web.trend import trend_points

COUNTS = {Metric.ADR_ATTENTION}


@dataclass(frozen=True)
class Tile:
    metric: Metric
    value: Value | None  # None when it couldn't be measured
    percent: int | None  # a share's rounded percent
    change: int | None  # since the previous update: percentage points for a share, the difference for a count
    trend_text: str  # each recorded value, oldest first, as the page writes it
    points: str  # the trend line's SVG points


def percent_of(value: Value) -> int | None:
    """A share's percent, rounded, but 100 only when it is all and 0 only when it is none."""
    if value.share is None:
        return None
    percent = round(value.share * 100)
    if value.numerator < (value.denominator or 0):
        percent = min(percent, 99)
    return max(percent, 1) if value.numerator > 0 else percent


def _number(metric: Metric, value: Value) -> float | None:
    if metric in COUNTS:
        return value.numerator
    return None if value.share is None else value.share * 100


def build_tiles(
    values: Mapping[Metric, Value], before: Mapping[Metric, Value], history: Mapping[Metric, Sequence[Value]]
) -> dict[Metric, Tile]:
    tiles = {}
    for metric in Metric:
        value, earlier = values.get(metric), before.get(metric)
        now = _number(metric, value) if value else None
        then = _number(metric, earlier) if earlier else None
        change = round(now - then) if now is not None and then is not None else None
        past = [recorded for recorded in history.get(metric, []) if _number(metric, recorded) is not None]
        trend = [number for number in (_number(metric, recorded) for recorded in past) if number is not None]
        if metric in COUNTS:
            text = ", ".join(str(recorded.numerator) for recorded in past)
        else:
            text = ", ".join(f"{percent_of(recorded)}%" for recorded in past)
        percent = None if value is None or metric in COUNTS else percent_of(value)
        tiles[metric] = Tile(metric, value, percent, change, text, trend_points(trend))
    return tiles


class ReportCache[T]:
    """One report, rebuilt only when its key changes: the latest snapshot, the guide's state and the date."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: object = None
        self._report: T | None = None

    def get(self, key: object, build: Callable[[], T]) -> T:
        with self._lock:
            if self._report is None or self._key != key:
                self._report = build()
                self._key = key
            return self._report
