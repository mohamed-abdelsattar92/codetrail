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

    def earlier(self) -> HistoryRange | None:
        moved = max(EARLIEST, self.calendar - CALENDAR_SHIFT)
        if calendar_span(moved) == calendar_span(self.calendar):
            return None
        return replace(self, calendar=moved)

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
    last = (
        _read_date(
            to_text,
            gettext("The end date {text} isn't a real date."),
            gettext("The end date {text} must lie between {earliest} and {latest}."),
        )
        if to_text
        else today
    )
    if from_text:
        first = _read_date(
            from_text,
            gettext("The start date {text} isn't a real date."),
            gettext("The start date {text} must lie between {earliest} and {latest}."),
        )
    else:
        first = max(EARLIEST, last - timedelta(days=default_days - 1))
    calendar = (
        _read_date(
            calendar_text,
            gettext("The calendar date {text} isn't a real date."),
            gettext("The calendar date {text} must lie between {earliest} and {latest}."),
        )
        if calendar_text
        else last
    )
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


def _read_date(text: str, not_real_message: str, out_of_bounds_message: str) -> date:
    """Read `YYYY-MM-DD`; the messages are already translated and hold `{text}`, `{earliest}`, `{latest}`."""
    not_real = RangeError(not_real_message.format(text=text[:MAX_ECHOED_TEXT]))
    if not ISO_DATE.fullmatch(text):
        raise not_real
    try:
        value = date.fromisoformat(text)
    except ValueError:
        raise not_real from None
    if not EARLIEST <= value <= LATEST:
        raise RangeError(out_of_bounds_message.format(text=text, earliest=EARLIEST, latest=LATEST))
    return value
