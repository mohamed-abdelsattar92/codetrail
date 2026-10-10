"""The branch's history and its commit types (design section 19.1): dates and counts only, never people."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

from codetrail.repo.history import WITHHELD_MESSAGE, Commit


@dataclass(frozen=True)
class Activity:
    commits: int
    merges: int
    first: date | None
    latest: date | None
    months: list[tuple[str, int]]  # "YYYY-MM" and its commits, the last few months up to today, oldest first
    years: list[tuple[int, int]]  # each year from the first commit's to the latest's, oldest first
    active_weeks: int
    weeks: int  # weeks from the first commit's to the latest's, both counted
    longest_gap: tuple[date, date] | None  # the two commit days furthest apart with none between
    cut: bool  # the history holds more commits than were read


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def measure_activity(times: Sequence[int], commits: int, merges: int, today: date, months: int) -> Activity:
    """Activity from author times in UTC; `commits` and `merges` count the whole history, even past the times read."""
    days = sorted(datetime.fromtimestamp(time, UTC).date() for time in times)
    by_month = Counter(day.strftime("%Y-%m") for day in days)
    shown, year, month = [], today.year, today.month
    for _ in range(months):
        shown.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    month_counts = [(key, by_month.get(key, 0)) for key in reversed(shown)]
    cut = len(times) < commits
    if not days:
        return Activity(commits, merges, None, None, month_counts, [], 0, 0, None, cut)
    first, latest = days[0], days[-1]
    by_year = Counter(day.year for day in days)
    years = [(year, by_year.get(year, 0)) for year in range(first.year, latest.year + 1)]
    active_weeks = len({_monday(day) for day in days})
    weeks = (_monday(latest) - _monday(first)).days // 7 + 1
    widest = max(pairwise(days), key=lambda pair: pair[1] - pair[0], default=None)
    gap = widest if widest and widest[1] > widest[0] else None
    return Activity(commits, merges, first, latest, month_counts, years, active_weeks, weeks, gap, cut)


@dataclass(frozen=True)
class CommitTypes:
    matched: int
    measured: int  # subjects read: withheld ones are left out
    types: list[tuple[str, int]]  # the most used first
    scopes: list[tuple[str, int]]

    @property
    def used(self) -> bool:
        """Whether the repository appears to use such subjects: at least half of them match."""
        return self.measured > 0 and self.matched * 2 >= self.measured


def _most_first(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def measure_types(commits: Iterable[Commit], pattern: re.Pattern[str], max_chars: int) -> CommitTypes:
    """Counts the subjects that match the target's commit type pattern; only the start of a subject is matched."""
    matched = measured = 0
    types: Counter[str] = Counter()
    scopes: Counter[str] = Counter()
    for commit in commits:
        if commit.subject == WITHHELD_MESSAGE:
            continue
        measured += 1
        found = pattern.match(commit.subject[:max_chars])
        if found is None:
            continue
        matched += 1
        types[found["type"].lower()] += 1
        scope = found.groupdict().get("scope")
        if scope:
            scopes[scope] += 1
    return CommitTypes(matched, measured, _most_first(types), _most_first(scopes))
