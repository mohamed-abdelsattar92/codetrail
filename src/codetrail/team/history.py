"""The past catch-ups page's dates and calendar (design section 25)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from urllib.parse import urlencode

EARLIEST = date(1970, 1, 1)
LATEST = date(9998, 12, 31)  # a day short of the end, so moving the calendar can't overflow
MAX_RANGE_DAYS = 3660
CALENDAR_WEEKS = 53
CALENDAR_SHIFT = timedelta(weeks=CALENDAR_WEEKS)  # 371 days
MAX_ECHOED_TEXT = 20
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)


class RangeError(ValueError):
    """The page's dates are not acceptable; the message says why, translated."""


@dataclass(frozen=True)
class HistoryRange:
    first: date
    last: date
    calendar: date

    def earlier(self) -> HistoryRange:
        return replace(self, calendar=max(EARLIEST, self.calendar - CALENDAR_SHIFT))

    def later(self, today: date) -> HistoryRange | None:
        if calendar_span(self.calendar)[1] >= today:
            return None
        return replace(self, calendar=min(today, self.calendar + CALENDAR_SHIFT))


@dataclass(frozen=True)
class CalendarDay:
    day: date
    count: int
    in_range: bool
    future: bool


def parse_range(
    from_text: str,
    to_text: str,
    calendar_text: str,
    today: date,
    default_days: int,
    gettext: Callable[[str], str],
) -> HistoryRange:
    last = _read_date(to_text, "end", gettext) if to_text else today
    if from_text:
        first = _read_date(from_text, "start", gettext)
    else:
        first = max(EARLIEST, last - timedelta(days=default_days - 1))
    calendar = _read_date(calendar_text, "calendar", gettext) if calendar_text else last
    if first > last:
        raise RangeError(gettext("The start date must not be after the end date."))
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise RangeError(gettext("The range is longer than {days} days.").format(days=MAX_RANGE_DAYS))
    return HistoryRange(first, last, calendar)


def calendar_span(end: date) -> tuple[date, date]:
    """The calendar's first Monday and last Sunday: 53 weeks, the last holding `end`."""
    last_monday = end - timedelta(days=end.weekday())
    return last_monday - timedelta(weeks=CALENDAR_WEEKS - 1), last_monday + timedelta(days=6)


def calendar_weeks(end: date, counts: Mapping[date, int], chosen: HistoryRange, today: date) -> list[list[CalendarDay]]:
    first_monday, _ = calendar_span(end)
    weeks = []
    for week_index in range(CALENDAR_WEEKS):
        week_start = first_monday + timedelta(weeks=week_index)
        days = [week_start + timedelta(days=offset) for offset in range(7)]
        weeks.append(
            [CalendarDay(day, counts.get(day, 0), chosen.first <= day <= chosen.last, day > today) for day in days]
        )
    return weeks


def history_query(chosen: HistoryRange, day: date | None = None) -> str:
    first, last = (day, day) if day else (chosen.first, chosen.last)
    return "?" + urlencode({"from": first.isoformat(), "to": last.isoformat(), "calendar": chosen.calendar.isoformat()})


def _read_date(text: str, name: str, gettext: Callable[[str], str]) -> date:
    try:
        if not ISO_DATE.fullmatch(text):
            raise ValueError(text)
        value = date.fromisoformat(text)
    except ValueError:
        raise RangeError(
            gettext("The {name} date {text} isn't a real date, written as YYYY-MM-DD.").format(
                name=name, text=text[:MAX_ECHOED_TEXT]
            )
        ) from None
    if not EARLIEST <= value <= LATEST:
        raise RangeError(
            gettext("The {name} date {text} must lie between {earliest} and {latest}.").format(
                name=name, text=text, earliest=EARLIEST, latest=LATEST
            )
        )
    return value
