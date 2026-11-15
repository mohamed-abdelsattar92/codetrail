"""The past catch-ups page's dates and calendar (design section 25)."""

import json
import logging
import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest

from codetrail.config import Paths
from codetrail.database import connect
from codetrail.guide import GuideRepository, Page
from codetrail.learn import LearningState
from codetrail.team.cards import load_overview
from codetrail.team.history import (
    HistoryRange,
    PastCatchUp,
    RangeError,
    calendar_span,
    calendar_weeks,
    digests_of,
    history_query,
    parse_range,
    past_catch_ups,
)
from codetrail.team.state import Overviews, TeamState, team_database
from tests.api.test_team_page import DIGEST, OVERVIEW, write_digest, write_overview
from tests.api.test_team_server import Team, following_team

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


# Past catch-ups, assembled from team.db, the overviews and the guides (design section 25.4).

TARGET = "acme-web-shop"
FOLLOWED = {TARGET: ("web-shop", "develop"), "acme-payments-api": ("payments-api", "develop")}
STARTED = "2026-11-11T10:00:00+00:00"
FINISHED = "2026-11-11T10:30:00+00:00"
CHOSEN = HistoryRange(date(2026, 11, 9), TODAY, TODAY)


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    with following_team(tmp_path, monkeypatch) as followed:
        yield followed


def add_record(
    paths: Paths,
    finished: str = FINISHED,
    started: str = STARTED,
    targets: list[str] | None = None,
    overview_id: str | None = None,
    failed: dict[str, str] | None = None,
    unticked: list[str] | None = None,
    digests: object = None,
) -> None:
    connection = connect(team_database(paths, "acme"))
    try:
        connection.execute(
            "INSERT INTO catch_ups (started_at, finished_at, targets, overview_id, failed, unticked, digests)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (started, finished, json.dumps([TARGET] if targets is None else targets), overview_id,
             json.dumps(failed or {}), json.dumps(unticked or []),
             None if digests is None else json.dumps(digests)),
        )  # fmt: skip
    finally:
        connection.close()


def write_digest_at(paths: Paths, digest_id: str, written_at: str, target: str = TARGET) -> None:
    meta: dict[str, object] = {"kind": "digest", "title": f"Digest {digest_id}", "from_commit": None,
                               "to_commit": "b" * 40, "written_at": written_at, "pages_changed": [],
                               "highlights": ["It changed."]}  # fmt: skip
    GuideRepository(paths.target_data(target) / "guide").write_page(Page(digest_id, meta, "Body."))


def assembled(team: Team, chosen: HistoryRange = CHOSEN, limit: int = 50) -> list[PastCatchUp]:
    catch_ups, _, _ = past_catch_ups(team.paths, "acme", chosen, FOLLOWED, 25, limit)
    return catch_ups


def test_recorded_digests_are_shown_in_order(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-a", "2026-11-11T10:10:00+00:00")
    write_digest_at(team.paths, "digests/2026-11-11-b", "2026-11-11T10:20:00+00:00")
    add_record(team.paths, digests=[[TARGET, "digests/2026-11-11-a"], [TARGET, "digests/2026-11-11-b"]])

    (catch_up,) = assembled(team)

    (repository,) = catch_up.repositories
    assert (repository.target, repository.repository, repository.followed) == (TARGET, "web-shop", True)
    assert [summary.id for summary in repository.digests] == ["digests/2026-11-11-b", "digests/2026-11-11-a"]
    assert repository.digests[0].link == f"/repos/{TARGET}/pages/digests/2026-11-11-b"
    assert repository.unread == {"digests/2026-11-11-a", "digests/2026-11-11-b"}


def test_a_digest_marked_read_is_not_unread(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-a", "2026-11-11T10:10:00+00:00")
    add_record(team.paths, digests=[[TARGET, "digests/2026-11-11-a"]])
    connection = connect(team.paths.target_data(TARGET) / "codetrail.db")
    try:
        LearningState(connection).mark_digest_read("digests/2026-11-11-a")
    finally:
        connection.close()

    (catch_up,) = assembled(team)

    assert catch_up.repositories[0].unread == set()


def test_an_older_catch_up_s_digests_are_matched_by_time(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-inside", "2026-11-11T10:15:00+00:00")
    write_digest_at(team.paths, "digests/2026-11-11-late", "2026-11-11T10:30:01+00:00")
    write_digest_at(team.paths, "digests/2026-11-11-early", "2026-11-11T09:59:59+00:00")
    write_digest_at(team.paths, "digests/2026-11-11-failed", "2026-11-11T10:15:00+00:00", "acme-payments-api")
    add_record(team.paths, targets=[TARGET, "acme-payments-api"], failed={"acme-payments-api": "Out of tokens."})

    (catch_up,) = assembled(team)

    assert [repository.target for repository in catch_up.repositories] == [TARGET]
    assert [summary.id for summary in catch_up.repositories[0].digests] == ["digests/2026-11-11-inside"]
    assert catch_up.failed == {"acme-payments-api": "Out of tokens."}


def test_digests_of_matches_by_time_only_without_a_record(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-inside", "2026-11-11T10:15:00+00:00")
    add_record(team.paths, digests=[])
    add_record(team.paths, finished="2026-11-12T10:30:00+00:00", started="2026-11-12T10:00:00+00:00")
    connection = connect(team_database(team.paths, "acme"))
    try:
        recorded, matched = TeamState(connection).finished_between(date(2026, 11, 11), date(2026, 11, 12))[::-1]
    finally:
        connection.close()

    assert digests_of(team.paths, recorded, "acme", {}) == {TARGET: []}  # recorded as none: no matching by time
    assert digests_of(team.paths, matched, "acme", {}) == {TARGET: []}  # its window holds nothing


def test_a_recorded_digest_the_guide_doesn_t_have_is_left_out(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-a", "2026-11-11T10:10:00+00:00")
    add_record(
        team.paths,
        digests=[[TARGET, "digests/2026-11-11-a"], [TARGET, "digests/2026-11-11-gone"], [TARGET, "../../etc/passwd"],
                 [TARGET, "concepts/orders"], ["../other", "digests/2026-11-11-a"]],
    )  # fmt: skip

    (catch_up,) = assembled(team)

    assert [summary.id for summary in catch_up.repositories[0].digests] == ["digests/2026-11-11-a"]


def test_a_target_no_longer_followed_keeps_its_target_name(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-a", "2026-11-11T10:10:00+00:00", "acme-inventory")
    add_record(team.paths, targets=["acme-inventory"], digests=[["acme-inventory", "digests/2026-11-11-a"]])
    read: list[Path] = []
    original = GuideRepository.pages

    def recording(self: GuideRepository, kind: str | None = None) -> list[Page]:
        read.append(self.root)
        return original(self, kind)

    monkeypatch.setattr(GuideRepository, "pages", recording)

    (catch_up,) = assembled(team)

    (repository,) = catch_up.repositories
    assert (repository.target, repository.repository, repository.followed) == (
        "acme-inventory",
        "acme-inventory",
        False,
    )
    assert repository.digests == []
    assert team.paths.target_data("acme-inventory") / "guide" not in read


def test_a_removed_guide_shows_no_digests(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-a", "2026-11-11T10:10:00+00:00")
    add_record(team.paths)
    shutil.rmtree(team.paths.target_data(TARGET) / "guide")

    (catch_up,) = assembled(team)

    assert catch_up.repositories[0].digests == []
    shutil.rmtree(team.paths.target_data(TARGET))
    assert assembled(team)[0].repositories[0].digests == []


def test_the_failed_and_unticked_are_copied_from_the_record(team: Team) -> None:
    add_record(team.paths, targets=[TARGET], failed={"acme-payments-api": "Timed out."}, unticked=["acme-inventory"])

    (catch_up,) = assembled(team)

    assert (catch_up.failed, catch_up.unticked) == ({"acme-payments-api": "Timed out."}, ["acme-inventory"])
    assert catch_up.overview is None


def test_the_overview_and_its_read_mark(team: Team) -> None:
    write_overview(team.paths)
    write_digest(team.paths)
    add_record(team.paths, overview_id=OVERVIEW)

    (catch_up,) = assembled(team)

    assert catch_up.overview_unread is True
    overview = load_overview(Overviews(team.paths.team_data("acme") / "overviews"), OVERVIEW, "acme")
    assert overview is not None and catch_up.overview == overview
    assert overview.digests == [(TARGET, "web-shop", DIGEST)]
    assert any("Both services now retry charges." in segment.html for segment in overview.segments)
    documented = [segment for segment in overview.segments if segment.kind == "rationale"]
    assert [segment.link for segment in documented] == [f"/repos/{TARGET}/source/services/api/main.py#L1"]

    connection = connect(team_database(team.paths, "acme"))
    try:
        TeamState(connection).mark_overview_read(OVERVIEW)
    finally:
        connection.close()

    (read,) = assembled(team)
    assert read.overview_unread is False
    assert read.overview == overview


def test_an_overview_id_that_fails_the_pattern_reads_as_none(team: Team) -> None:
    add_record(team.paths, overview_id="overviews/../../etc/passwd")

    (catch_up,) = assembled(team)

    assert catch_up.overview is None
    assert load_overview(Overviews(team.paths.team_data("acme") / "overviews"), "overviews/nope", "acme") is None


def test_a_corrupt_digests_column_leaves_that_catch_up_out(team: Team) -> None:
    add_record(team.paths, digests={"not": "a list"})
    add_record(team.paths, finished="2026-11-12T10:30:00+00:00", started="2026-11-12T10:00:00+00:00")

    catch_ups = assembled(team)

    assert [catch_up.record.finished_at for catch_up in catch_ups] == ["2026-11-12T10:30:00+00:00"]


def test_the_list_is_capped_and_the_calendar_counts_all(team: Team) -> None:
    first_day = date(2026, 9, 20)
    for index in range(500):
        day = first_day + timedelta(days=index % 50)
        stamp = f"{day.isoformat()}T{index // 50:02d}:30:00+00:00"
        add_record(team.paths, finished=stamp, started=stamp)
    chosen = HistoryRange(first_day, first_day + timedelta(days=49), TODAY)

    catch_ups, left_out, counts = past_catch_ups(team.paths, "acme", chosen, FOLLOWED, 25, 50)

    assert len(catch_ups) == 50
    assert left_out == 450
    assert sum(counts.values()) == 500
    assert counts[first_day] == 10
    finished = [catch_up.record.finished_at for catch_up in catch_ups]
    assert finished == sorted(finished, reverse=True)


def test_the_counts_cover_the_calendar_and_the_list_only_the_range(team: Team) -> None:
    add_record(team.paths)  # 2026-11-11, in the range
    add_record(team.paths, finished="2026-03-02T10:30:00+00:00", started="2026-03-02T10:00:00+00:00")
    add_record(team.paths, finished="2024-03-02T10:30:00+00:00", started="2024-03-02T10:00:00+00:00")

    catch_ups, left_out, counts = past_catch_ups(team.paths, "acme", CHOSEN, FOLLOWED, 25, 50)

    assert len(catch_ups) == 1 and left_out == 0
    assert counts == {date(2026, 11, 11): 1, date(2026, 3, 2): 1}


def test_a_team_that_never_caught_up_makes_no_database(team: Team) -> None:
    assert past_catch_ups(team.paths, "acme", CHOSEN, FOLLOWED, 25, 50) == ([], 0, {})
    assert not team_database(team.paths, "acme").exists()


def test_a_target_whose_name_is_not_valid_is_left_out(team: Team) -> None:
    add_record(team.paths, targets=[TARGET, "../other"])

    (catch_up,) = assembled(team)

    assert [repository.target for repository in catch_up.repositories] == [TARGET]


def test_digests_written_exactly_at_the_start_and_the_finish_are_found(team: Team) -> None:
    write_digest_at(team.paths, "digests/2026-11-11-start", STARTED)
    write_digest_at(team.paths, "digests/2026-11-11-finish", FINISHED)
    add_record(team.paths)

    (catch_up,) = assembled(team)

    ids = {summary.id for summary in catch_up.repositories[0].digests}
    assert ids == {"digests/2026-11-11-start", "digests/2026-11-11-finish"}


def test_a_corrupt_digest_file_leaves_that_target_without_digests_beside_a_good_catch_up(
    team: Team, caplog: pytest.LogCaptureFixture
) -> None:
    write_digest_at(team.paths, "digests/2026-11-12-a", "2026-11-12T10:10:00+00:00", "acme-payments-api")
    add_record(team.paths, targets=["acme-payments-api"], finished="2026-11-12T10:30:00+00:00",
               started="2026-11-12T10:00:00+00:00")  # fmt: skip
    write_digest_at(team.paths, "digests/2026-11-11-a", "2026-11-11T10:10:00+00:00")
    folder = team.paths.target_data(TARGET) / "guide" / "digests"
    (folder / "2026-11-11-broken.md").write_text("---\n: [unclosed\n---\nSECRET body.\n", encoding="utf-8")
    (folder / "2026-11-11-binary.md").write_bytes(b"\xff\xfe\x00")
    add_record(team.paths)

    with caplog.at_level(logging.WARNING):
        catch_ups, _, _ = past_catch_ups(team.paths, "acme", CHOSEN, FOLLOWED, 25, 50)

    assert [len(catch_up.repositories) for catch_up in catch_ups] == [1, 1]
    by_target = {catch_up.repositories[0].target: catch_up.repositories[0] for catch_up in catch_ups}
    assert by_target[TARGET].digests == []
    assert [summary.id for summary in by_target["acme-payments-api"].digests] == ["digests/2026-11-12-a"]
    assert "team 'acme'" in caplog.text and TARGET in caplog.text and "SECRET" not in caplog.text


def test_each_guide_is_read_once_for_a_page_of_catch_ups(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    for day in ("11", "12"):
        add_record(team.paths, finished=f"2026-11-{day}T10:30:00+00:00", started=f"2026-11-{day}T10:00:00+00:00")
    reads: list[Path] = []
    original = GuideRepository.pages

    def recording(self: GuideRepository, kind: str | None = None) -> list[Page]:
        reads.append(self.root)
        return original(self, kind)

    monkeypatch.setattr(GuideRepository, "pages", recording)

    assert len(assembled(team)) == 2
    assert reads == [team.paths.target_data(TARGET) / "guide"]
