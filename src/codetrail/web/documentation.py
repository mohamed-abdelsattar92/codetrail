"""The Documentation page's tiles, its sections' helpers, and the report kept in memory between updates.

Design sections 18.4 and 21.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from codetrail.facts import Entity
from codetrail.metrics.decisions import written_on
from codetrail.metrics.report import Metric, Value
from codetrail.repo.history import Commit
from codetrail.web.trend import trend_points

COUNTS = {Metric.ADR_ATTENTION, Metric.COMMITS, Metric.CODE_LINES, Metric.SOURCE_FILES}
# The sections a share can open, in their order on the page (design section 21.1).
SECTIONS = (
    ("rationale", Metric.DOCUMENTED_SHARE),
    ("commits", Metric.COMMIT_WHY_SHARE),
    ("coverage", Metric.MENTIONED_SHARE),
)


def section_to_open(values: Mapping[Metric, Value]) -> str | None:
    """The decisions when an ADR needs attention; else the lowest measured share below 1, the first on a tie."""
    attention = values.get(Metric.ADR_ATTENTION)
    if attention is not None and attention.numerator:
        return "decisions"
    shares = []
    for order, (section, metric) in enumerate(SECTIONS):
        value = values.get(metric)
        if value is not None and value.share is not None and value.share < 1:
            shares.append((value.share, order, section))
    return min(shares)[2] if shares else None


def commit_kind(commit: Commit) -> str:
    """A commit without a why: it has a body that doesn't say why, or only a subject."""
    return "body-only" if commit.body else "subject-only"


@dataclass(frozen=True)
class Filter:
    kinds: list[str]  # each item's kind, in the list's order
    totals: dict[str, int]  # items per kind, "all" first
    folded: dict[str, int]  # items past the first `listed`, per kind, "all" first


def filtered(kinds: Sequence[str], listed: int) -> Filter:
    """What a list's filter chips need: each item's kind, and the items of each kind in all and in the folded rest."""
    rest = Counter(kinds[listed:])
    totals = {"all": len(kinds), **Counter(kinds)}
    folded = {kind: len(kinds[listed:]) if kind == "all" else rest.get(kind, 0) for kind in totals}
    return Filter(list(kinds), totals, folded)


def segments(parts: Sequence[tuple[str, int, str]], width: int = 1000) -> list[tuple[str, float, float]]:
    """A stacked bar's parts as tone, x and width; a part with no tone takes its room and isn't drawn."""
    total = sum(count for _, count, _ in parts)
    drawn, x = [], 0.0
    for tone, count, _ in parts:
        if count <= 0 or not total:
            continue
        span = count / total * width
        if tone:
            drawn.append((tone, round(x, 1), round(span, 1)))
        x += span
    return drawn


def days_proposed(decision: Entity, today: date) -> int | None:
    written = written_on(decision)
    return (today - written).days if written is not None else None


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


def age(first: date, today: date) -> tuple[int, int]:
    """Whole months from `first` to `today`, as years and months; a day of the month not yet reached doesn't count."""
    months = (today.year - first.year) * 12 + today.month - first.month - (1 if today.day < first.day else 0)
    return divmod(max(months, 0), 12)


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
