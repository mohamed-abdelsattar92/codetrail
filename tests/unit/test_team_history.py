"""The past catch-ups page's dates and calendar (design section 25)."""

from datetime import date, timedelta

import pytest

from codetrail.team.history import (
    HistoryRange,
    RangeError,
    calendar_span,
    calendar_weeks,
    history_query,
    parse_range,
)

TODAY = date(2026, 11, 15)
ARABIC_INDIC_ZERO = 0x0660
ARABIC_INDIC_DATE = "2026-11-10".translate({ord(digit): chr(ARABIC_INDIC_ZERO + int(digit)) for digit in "0123456789"})


def untranslated(text: str) -> str:
    return text


def parse(from_text: str = "", to_text: str = "", calendar_text: str = "") -> HistoryRange:
    return parse_range(from_text, to_text, calendar_text, TODAY, 7, untranslated)


def test_the_default_range_is_the_last_history_days_ending_today() -> None:
    assert parse() == HistoryRange(date(2026, 11, 9), date(2026, 11, 15), date(2026, 11, 15))


def test_a_missing_end_takes_its_default() -> None:
    only_to = parse(to_text="2026-11-01")
    assert only_to.first == date(2026, 10, 26)
    assert only_to.last == date(2026, 11, 1)
    assert only_to.calendar == date(2026, 11, 1)
    only_from = parse(from_text="2026-11-10")
    assert only_from.first == date(2026, 11, 10)
    assert only_from.last == TODAY
    only_calendar = parse(calendar_text="2026-06-01")
    assert (only_calendar.first, only_calendar.last) == (date(2026, 11, 9), TODAY)
    assert only_calendar.calendar == date(2026, 6, 1)


@pytest.mark.parametrize(
    ("from_text", "to_text", "calendar_text"),
    [
        ("2026-02-30", "", ""),
        ("2026-1-05", "", ""),
        (ARABIC_INDIC_DATE, "", ""),
        ("2026-11-10T00:00", "", ""),
        ("2026-11-12", "2026-11-10", ""),
        ("", "", "2026-02-30"),
        ("0001-01-01", "", ""),
        ("1969-12-31", "", ""),
        ("", "9999-12-31", ""),
        ("", "", "0001-01-01"),
        ("", "", "9999-12-31"),
    ],
)
def test_dates_refused(from_text: str, to_text: str, calendar_text: str) -> None:
    with pytest.raises(RangeError):
        parse(from_text, to_text, calendar_text)


def test_a_range_of_exactly_the_limit_is_accepted_and_one_more_day_is_not() -> None:
    last = date(2026, 11, 15)
    accepted = parse((last - timedelta(days=3659)).isoformat(), last.isoformat())
    assert (accepted.last - accepted.first).days + 1 == 3660
    with pytest.raises(RangeError):
        parse((last - timedelta(days=3660)).isoformat(), last.isoformat())


def test_a_range_error_names_the_problem_through_gettext() -> None:
    with pytest.raises(RangeError, match="isn't a real date") as caught:
        parse_range("2026-02-30", "", "", TODAY, 7, lambda text: f"<{text}>")
    assert str(caught.value).startswith("<")


def test_a_future_to_is_allowed() -> None:
    assert parse(to_text="2026-12-31").last == date(2026, 12, 31)


def test_the_default_range_near_the_earliest_date_is_cut_there() -> None:
    chosen = parse(to_text="1970-01-03")
    assert chosen.first == date(1970, 1, 1)


def test_the_calendar_ends_with_calendar_s_week() -> None:
    chosen = HistoryRange(date(2026, 11, 9), TODAY, date(2026, 11, 12))
    weeks = calendar_weeks(chosen.calendar, {}, chosen, TODAY)
    assert len(weeks) == 53
    assert all(len(week) == 7 for week in weeks)
    assert weeks[-1][0].day == date(2026, 11, 9)
    assert weeks[0][0].day == date(2025, 11, 10)
    assert calendar_span(chosen.calendar) == (date(2025, 11, 10), date(2026, 11, 15))
    assert [day.future for day in weeks[-1]] == [False] * 7
    assert weeks[-1][-1].day == date(2026, 11, 15)
    early = calendar_weeks(date(2026, 11, 10), {}, chosen, date(2026, 11, 11))
    assert [day.future for day in early[-1]] == [False, False, False, True, True, True, True]


def test_the_calendar_marks_the_range_and_counts() -> None:
    chosen = HistoryRange(date(2026, 11, 9), date(2026, 11, 15), date(2026, 11, 15))
    weeks = calendar_weeks(chosen.calendar, {date(2026, 11, 11): 2}, chosen, TODAY)
    days = [day for week in weeks for day in week]
    assert {day.day for day in days if day.in_range} == {
        date(2026, 11, 9) + timedelta(days=offset) for offset in range(7)
    }
    assert [day.count for day in days if day.day == date(2026, 11, 11)] == [2]
    assert sum(day.count for day in days) == 2


def test_moving_the_calendar_keeps_the_range() -> None:
    chosen = HistoryRange(date(2026, 11, 9), date(2026, 11, 15), date(2026, 11, 15))
    assert chosen.earlier() == HistoryRange(chosen.first, chosen.last, date(2026, 11, 15) - timedelta(days=371))
    assert chosen.later(TODAY) is None
    from_last_year = HistoryRange(chosen.first, chosen.last, date(2025, 11, 12))
    later = from_last_year.later(TODAY)
    assert later == HistoryRange(chosen.first, chosen.last, date(2026, 11, 15))
    from_older = HistoryRange(chosen.first, chosen.last, date(2024, 11, 12))
    older_later = from_older.later(TODAY)
    assert older_later is not None
    assert older_later.calendar == date(2025, 11, 18)


def test_moving_the_calendar_never_leaves_the_accepted_dates() -> None:
    earliest = HistoryRange(date(1970, 1, 1), date(1970, 1, 2), date(1970, 1, 1))
    assert earliest.earlier() is None
    same_week = HistoryRange(date(1970, 1, 1), date(1970, 1, 2), date(1970, 1, 3))
    assert same_week.earlier() is None
    near = HistoryRange(date(1970, 6, 1), date(1970, 6, 2), date(1970, 6, 1))
    near_earlier = near.earlier()
    assert near_earlier is not None
    assert near_earlier.calendar == date(1970, 1, 1)
    latest = HistoryRange(date(2026, 1, 1), date(2026, 1, 2), date(9998, 12, 31))
    assert latest.later(date(9998, 12, 31)) is None
    assert calendar_weeks(date(9998, 12, 31), {}, latest, TODAY)[-1][-1].day <= date(9999, 1, 6)


def test_a_day_s_link_keeps_the_calendar() -> None:
    chosen = HistoryRange(date(2026, 11, 9), TODAY, date(2026, 11, 12))
    assert history_query(chosen, date(2026, 11, 11)) == "?from=2026-11-11&to=2026-11-11&calendar=2026-11-12"
    assert history_query(chosen) == "?from=2026-11-09&to=2026-11-15&calendar=2026-11-12"
