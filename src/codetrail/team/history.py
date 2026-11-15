"""The past catch-ups page's dates and calendar (design section 25)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlencode

from codetrail.config import TARGET_NAME, Paths
from codetrail.database import connect
from codetrail.guide import PAGE_ID, GuideRepository, Page
from codetrail.learn import LearningState
from codetrail.team.cards import DigestSummary, Overview, digest_summaries, load_overview
from codetrail.team.state import CatchUpRecord, Overviews, TeamState, team_database

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


@dataclass(frozen=True)
class PastRepository:
    """One repository a catch-up ticked and didn't fail, with the digests it wrote."""

    target: str
    repository: str  # the service's name for it while it is followed, else the target name
    followed: bool
    digests: list[DigestSummary]  # newest first
    unread: set[str]  # the ids of those digests not yet read


@dataclass(frozen=True)
class PastCatchUp:
    record: CatchUpRecord
    overview: Overview | None
    overview_unread: bool
    repositories: list[PastRepository]
    failed: dict[str, str]
    unticked: list[str]


def past_catch_ups(
    paths: Paths,
    name: str,
    chosen: HistoryRange,
    followed: Mapping[str, tuple[str, str]],
    max_nodes: int,
    limit: int,
) -> tuple[list[PastCatchUp], int, dict[date, int]]:
    """The catch-ups in the range (at most `limit`, newest first), how many it left out, and the number finished on
    each day of the calendar's 53 weeks. A team with no database gives nothing, and none is made."""
    database = team_database(paths, name)
    if not database.exists():
        return [], 0, {}
    connection = connect(database)
    try:
        state = TeamState(connection)
        counts: dict[date, int] = {}
        for record in state.finished_between(*calendar_span(chosen.calendar)):
            day = datetime.fromisoformat(record.finished_at).astimezone(UTC).date()
            counts[day] = counts.get(day, 0) + 1
        records = state.finished_between(chosen.first, chosen.last)
        shown = records[:limit]
        overviews = Overviews(paths.team_data(name) / "overviews")
        found = [_digest_pages(paths, record, followed) for record in shown]
        unread = _unread_digests(paths, found)
        past = []
        for record, pages in zip(shown, found, strict=True):
            overview_id = record.overview_id
            overview = None if overview_id is None else load_overview(overviews, overview_id)
            repositories = [
                PastRepository(
                    target,
                    followed[target][0] if target in followed else target,
                    target in followed,
                    digest_summaries(target, pages[target], max_nodes),
                    {page.id for page in pages[target]} & unread[target],
                )
                for target in record.targets
                if target in pages
            ]
            past.append(
                PastCatchUp(
                    record,
                    overview,
                    overview is not None and overview_id is not None and not state.overview_read(overview_id),
                    repositories,
                    record.failed,
                    record.unticked,
                )
            )
    finally:
        connection.close()
    return past, len(records) - len(shown), counts


def _digest_pages(
    paths: Paths, record: CatchUpRecord, followed: Mapping[str, tuple[str, str]]
) -> dict[str, list[Page]]:
    """The catch-up's digests per ticked target that didn't fail, newest first. A target the team no longer follows
    has none: a target is read only if the team still has it."""
    found = digests_of(paths, record)
    return {
        target: sorted(found.get(target, []) if target in followed else [],
                       key=lambda page: str(page.meta.get("written_at", "")), reverse=True)
        for target in record.targets
        if target not in record.failed
    }  # fmt: skip


def _unread_digests(paths: Paths, found: list[dict[str, list[Page]]]) -> dict[str, set[str]]:
    """Each target's unread digest ids among those shown, from its learning state opened once. A target without a
    database has no read marks, so all its digests are unread."""
    ids: dict[str, set[str]] = {}
    for pages in found:
        for target, digests in pages.items():
            ids.setdefault(target, set()).update(page.id for page in digests)
    unread = {}
    for target, digest_ids in ids.items():
        database = paths.target_data(target) / "codetrail.db"
        if not digest_ids or not database.exists():
            unread[target] = digest_ids
            continue
        connection = connect(database)
        try:
            unread[target] = set(LearningState(connection).unread_digests(sorted(digest_ids)))
        finally:
            connection.close()
    return unread


def digests_of(paths: Paths, record: CatchUpRecord) -> dict[str, list[Page]]:
    """The digest pages of each target the catch-up caught up (ticked, not failed): the ones its record lists, or, for
    a catch-up from before design 25, the ones whose `written_at` lies in the time it ran. A target whose guide is
    gone, or a recorded id the guide doesn't have as a digest, gives nothing."""
    result: dict[str, list[Page]] = {}
    for target in record.targets:
        if target in record.failed or not TARGET_NAME.fullmatch(target):
            continue
        guide = GuideRepository(paths.target_data(target) / "guide")
        try:
            available = {page.id: page for page in guide.pages("digest")}
        except OSError:
            available = {}
        if record.digests is None:
            result[target] = [
                page
                for page in available.values()
                if record.started_at <= str(page.meta.get("written_at", "")) <= record.finished_at
            ]
        else:
            wanted = [
                digest_id for owner, digest_id in record.digests if owner == target and PAGE_ID.fullmatch(digest_id)
            ]
            result[target] = [available[digest_id] for digest_id in wanted if digest_id in available]
    return result
