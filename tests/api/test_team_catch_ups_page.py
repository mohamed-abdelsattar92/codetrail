"""The past catch-ups page: its range, its calendar, and each catch-up as the team page showed it (design section 25).

Team acme follows web-shop, payments-api and inventory (see tests/api/test_team_server.py). The catch-ups are written
straight into the team's database, so each finishes when a test says; the page reads them and spends nothing.
"""

import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant.estimate import when_text
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths, TeamSettings
from codetrail.database import connect
from codetrail.hosting.fake import FakeHosting
from codetrail.team import follow, server
from codetrail.team.state import team_database
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_team_page import (
    DIGEST,
    HOSTILE,
    OVERVIEW,
    finish_catch_up,
    header,
    post,
    write_digest,
    write_overview,
)
from tests.api.test_team_server import ORIGIN, SETTINGS, TARGETS, Team, app_for, client_for, following_team, sidebar
from tests.unit.test_catalogs import EASTERN_DIGITS

DAY = date(2026, 11, 11)
ONE_DAY = f"?from={DAY}&to={DAY}&calendar={DAY}"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    with following_team(tmp_path, monkeypatch) as followed:
        yield followed


def today() -> date:
    return datetime.now(UTC).date()


def at(day: date, hour: int, minute: int = 0) -> str:
    """A time as a catch-up's record holds it."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC).isoformat(timespec="seconds")


def record(
    paths: Paths,
    finished_at: str,
    targets: list[str] | None = None,
    overview_id: str | None = None,
    problem: str | None = None,
    failed: Mapping[str, str] | None = None,
    unticked: list[str] | None = None,
    digests: list[tuple[str, str]] | None = None,
) -> None:
    """A catch-up that ran for the five minutes before `finished_at`."""
    started_at = (datetime.fromisoformat(finished_at) - timedelta(minutes=5)).isoformat(timespec="seconds")
    connection = connect(team_database(paths, "acme"))
    try:
        connection.execute(
            "INSERT INTO catch_ups (started_at, finished_at, targets, overview_id, overview_problem, failed, unticked,"
            " digests) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, json.dumps(["acme-web-shop"] if targets is None else targets), overview_id,
             problem, json.dumps(dict(failed or {})), json.dumps(unticked or []),
             None if digests is None else json.dumps([list(pair) for pair in digests])),
        )  # fmt: skip
    finally:
        connection.close()


def catch_ups(html: str) -> list[str]:
    """Each catch-up's folding section, newest first."""
    return re.findall(r"<details[^>]*data-catch-up=.*?</details>", html, re.DOTALL)


def test_a_read_overview_still_shows_here(team: Team) -> None:
    write_overview(team.paths)
    write_digest(team.paths)
    finished_at = finish_catch_up(team.paths, OVERVIEW)
    session = SessionState(60)
    client = client_for(team, session)
    [unread] = catch_ups(client.get("/team/catch-ups").text)
    assert "Both services now retry charges." in unread and '<span class="pill pill-read"' in unread

    assert post(client, "/team/overview/read", {"overview_id": OVERVIEW}, session.token) == 204

    assert "overview-title" not in client.get("/").text
    [shown] = catch_ups(client.get("/team/catch-ups").text)
    assert "Across the team" in shown and "Both services now retry charges." in shown
    assert 'lang="en" dir="ltr"' in shown
    assert "pill" not in shown and "data-overview-read" not in shown
    assert finished_at[:10] == today().isoformat()


def test_a_catch_up_s_digests_show_without_mark_read(team: Team) -> None:
    write_digest(team.paths)
    record(team.paths, at(DAY, 10), digests=[("acme-web-shop", DIGEST)])

    [shown] = catch_ups(client_for(team).get(f"/team/catch-ups{ONE_DAY}").text)

    assert '<a href="/repos/acme-web-shop/"><bdi>web-shop</bdi></a>' in shown
    assert f'<a href="/repos/acme-web-shop/pages/{DIGEST}"><bdi>Charges retry twice</bdi></a>' in shown
    assert "<strong>twice</strong>" in shown and "1 guide page changed" in shown
    assert '<span class="pill pill-read" lang="en" dir="ltr">unread</span>' in shown
    assert "data-digest-read" not in shown and "Mark read" not in shown


def test_one_day_and_a_stretch(team: Team) -> None:
    morning, evening, before = at(DAY, 9), at(DAY, 15), at(DAY - timedelta(days=1), 12)
    record(team.paths, morning)
    record(team.paths, evening, targets=["acme-web-shop", "acme-payments-api"])
    record(team.paths, before)
    client = client_for(team)

    day = catch_ups(client.get(f"/team/catch-ups{ONE_DAY}").text)
    stretch = catch_ups(client.get(f"/team/catch-ups?from={DAY - timedelta(days=1)}&to={DAY}&calendar={DAY}").text)

    assert len(day) == 2 and all(section.startswith("<details open") for section in day)
    assert len(stretch) == 3
    assert [section.startswith("<details open") for section in stretch] == [True, False, False]
    newest = stretch[0]
    assert f'<time datetime="{evening}" dir="ltr">{when_text(evening)}</time>' in newest
    assert "2 repositories caught up" in newest
    assert "1 repository caught up" in stretch[1] and when_text(morning) in stretch[1]
    assert when_text(before) in stretch[2]


def test_overview_skipped_missing_and_failed(team: Team) -> None:
    record(team.paths, at(DAY, 9))  # skipped: no overview, and no problem
    record(team.paths, at(DAY, 10), overview_id=OVERVIEW)  # its file is missing
    record(team.paths, at(DAY, 11), problem="The overview failed its checks twice: <b>too long</b>")

    failed, missing, skipped = catch_ups(client_for(team).get(f"/team/catch-ups{ONE_DAY}").text)

    problem = "The overview failed its checks twice: &lt;b&gt;too long&lt;/b&gt;"
    assert "Across the team" in failed and f"This catch-up has no overview: <bdi>{problem}</bdi>" in failed
    assert "Across the team" not in missing and "no overview" not in missing
    assert "Across the team" not in skipped and "no overview" not in skipped


def test_the_rest_line_lists_failed_and_unticked_only(team: Team) -> None:
    record(team.paths, at(DAY, 10), targets=["acme-web-shop", "acme-payments-api"],
           failed={"acme-payments-api": "The plan was <refused>."}, unticked=["acme-inventory"])  # fmt: skip

    [shown] = catch_ups(client_for(team).get(f"/team/catch-ups{ONE_DAY}").text)

    rest = re.search(r'<p class="team-quiet catch-up-rest">.*?</p>', shown, re.DOTALL)
    assert rest is not None, shown
    assert "<bdi>payments-api</bdi> (<bdi>The plan was &lt;refused&gt;.</bdi>)" in rest.group(0)
    assert "Left out: <bdi>inventory</bdi>" in rest.group(0)
    assert "web-shop" not in rest.group(0) and "Nothing new" not in shown
    assert "1 repository caught up" in shown


def test_a_catch_up_with_nothing_missed_has_no_rest_line(team: Team) -> None:
    record(team.paths, at(DAY, 10))

    [shown] = catch_ups(client_for(team).get(f"/team/catch-ups{ONE_DAY}").text)

    assert "catch-up-rest" not in shown


def test_a_bad_date_is_a_400_that_names_it(team: Team) -> None:
    client = client_for(team)

    impossible = client.get("/team/catch-ups?from=2026-02-30")
    hostile = client.get("/team/catch-ups?to=<script>")
    too_long = client.get("/team/catch-ups?to=2026-11-110")

    assert impossible.status_code == 400
    assert "The start date 2026-02-30 isn&#39;t a real date." in impossible.text
    assert '<main id="content"' in impossible.text  # in the team's shell
    assert hostile.status_code == 400
    assert "The end date &lt;script&gt; isn&#39;t a real date." in hostile.text and "<script>" not in hostile.text
    assert too_long.status_code == 422  # refused by the query's length limit, before it is parsed


def test_the_calendar_links_days_with_catch_ups(team: Team) -> None:
    now = today()
    day, quiet = now - timedelta(days=4), now - timedelta(days=5)
    record(team.paths, at(day, 10))

    page = client_for(team).get("/team/catch-ups").text

    link = f'<a href="/team/catch-ups?from={day}&amp;to={day}&amp;calendar={now}" aria-label="{day}: 1 catch-up">'
    assert link in page
    assert f"<title>{quiet}: 0 catch-ups</title>" in page
    assert re.search(rf'<a [^>]*aria-label="{quiet}:', page) is None
    assert f"from={quiet}&amp;to={quiet}" not in page
    assert "Earlier" in page and "Later" not in page
    assert re.search(r'class="day heat-1 in-range"', page) is not None


def test_earlier_moves_the_calendar_and_keeps_the_range(team: Team) -> None:
    record(team.paths, at(DAY, 10))
    client = client_for(team)
    first, last = date(2026, 11, 9), date(2026, 11, 15)
    earlier = last - timedelta(weeks=53)

    page = client.get(f"/team/catch-ups?from={first}&to={last}&calendar={last}").text

    move = f'href="/team/catch-ups?from={first}&amp;to={last}&amp;calendar={earlier}"'
    assert move in page
    moved = client.get(f"/team/catch-ups?from={first}&to={last}&calendar={earlier}").text
    assert f'name="from" value="{first}"' in moved and f'name="to" value="{last}"' in moved
    assert f'href="/team/catch-ups?from={first}&amp;to={last}&amp;calendar={min(today(), last)}"' in moved
    assert "Later" in moved


def test_a_range_past_the_calendar_says_so(team: Team) -> None:
    record(team.paths, at(DAY, 10))
    client = client_for(team)
    last = date(2026, 11, 15)

    inside = client.get(f"/team/catch-ups?from={last - timedelta(days=6)}&to={last}&calendar={last}").text
    before = client.get(f"/team/catch-ups?from=2025-01-01&to={last}&calendar={last}").text
    after = client.get(f"/team/catch-ups?from={last}&to={last}&calendar={last - timedelta(weeks=53)}").text

    assert "The range starts before this calendar." not in inside and "ends after" not in inside
    note = re.search(r'<div class="calendar-layout">.*?</section>', before, re.DOTALL)
    assert note is not None and "The range starts before this calendar." in note.group(0)
    assert "The range ends after this calendar." in after


def test_the_cap_s_note(team: Team) -> None:
    record(team.paths, at(DAY, 9))
    record(team.paths, at(DAY, 10))
    session = SessionState(60)
    settings = SETTINGS.model_copy(update={"team": TeamSettings(max_history_catch_ups=1)})
    app = server.create_team_app(team.paths, "acme", session, settings, team.services, team.hold)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303

    page = client.get(f"/team/catch-ups{ONE_DAY}").text

    [shown] = catch_ups(page)
    assert when_text(at(DAY, 10)) in shown
    assert "1 more catch-up in this range isn't shown; a narrower range shows it." in page
    assert re.search(r'class="day heat-2', page) is not None  # the calendar counts both


def test_the_team_page_links_here_once_a_catch_up_finished(team: Team) -> None:
    client = client_for(team)
    assert 'href="/team/catch-ups"' not in header(client.get("/").text)

    finish_catch_up(team.paths)

    assert '<a href="/team/catch-ups">Past catch-ups</a>' in header(client.get("/").text)
    for path in ("/", "/team/add", "/team/catch-ups"):
        side = sidebar(client.get(path).text)
        assert side.index('href="/team/add"') < side.index('href="/team/catch-ups"') < side.index("/repos/"), path
    current = sidebar(client.get("/team/catch-ups").text)
    assert re.search(r'<a class="nav-link" href="/team/catch-ups" aria-current="page">.*?Past catch-ups</span></a>',
                     current, re.DOTALL)  # fmt: skip


def test_the_page_needs_the_session(team: Team) -> None:
    signed_out = TestClient(app_for(team, SessionState(60)), base_url=ORIGIN, follow_redirects=False)  # type: ignore[arg-type]

    refused = signed_out.get("/team/catch-ups")

    assert refused.status_code == signed_out.get("/").status_code == 403
    assert refused.text == "Sign in with the link codetrail serve printed."
    assert client_for(team).get("/team/catch-ups", headers={"host": "evil.example"}).status_code == 400


def modification_times(paths: Paths) -> dict[Path, int]:
    """team.db's, and every file's in each guide."""
    files = [team_database(paths, "acme")]
    for target in TARGETS:
        files += [found for found in (paths.target_data(target) / "guide").rglob("*") if found.is_file()]
    return {found: found.stat().st_mtime_ns for found in files}


def test_the_page_spends_and_writes_nothing(team: Team) -> None:
    write_overview(team.paths)
    write_digest(team.paths)
    record(team.paths, at(DAY, 10), overview_id=OVERVIEW, digests=[("acme-web-shop", DIGEST)])
    writer, overview_writer = FakeAssistant(), FakeAssistant()
    session = SessionState(60)
    app = server.create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.hold,
                                 writer_for=lambda target: writer, overview_writer=overview_writer)  # fmt: skip
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    before = modification_times(team.paths)

    page = client.get(f"/team/catch-ups{ONE_DAY}")

    assert page.status_code == 200 and "Both services now retry charges." in page.text and "Charges retry" in page.text
    assert modification_times(team.paths) == before
    assert writer.requests == [] and overview_writer.requests == []
    service = team.services["github"]
    assert isinstance(service, FakeHosting) and service.calls == []


def test_the_arabic_page(team: Team) -> None:
    write_overview(team.paths)
    record(team.paths, at(DAY, 10), overview_id=OVERVIEW)
    session = SessionState(60)
    client = client_for(team, session)
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    assert client.post("/settings/language", json={"language": "ar"}, headers=headers).status_code == 204

    page = client.get(f"/team/catch-ups{ONE_DAY}").text

    assert '<html lang="ar" dir="rtl">' in page
    assert "<title>المواكبات السابقة · Codetrail</title>" in page
    assert EASTERN_DIGITS.search(page) is None  # every date and count in Western digits
    assert f'<time datetime="{at(DAY, 10)}" dir="ltr">{when_text(at(DAY, 10))}</time>' in page
    assert '<div class="calendar" dir="ltr">' in page
    [shown] = catch_ups(page)
    assert '<div class="prose team-overview-body" lang="en" dir="ltr">' in shown


def test_untrusted_names_are_escaped(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    followed = follow.followed_targets

    def hostile_names(*arguments: object) -> dict[str, tuple[str, str]]:
        targets = followed(*arguments)  # type: ignore[arg-type]
        return {target: (HOSTILE if target == "acme-web-shop" else repository, branch)
                for target, (repository, branch) in targets.items()}  # fmt: skip

    monkeypatch.setattr("codetrail.team.server.followed_targets", hostile_names)
    record(team.paths, at(DAY, 10), targets=["acme-web-shop", "acme-payments-api"],
           failed={"acme-payments-api": HOSTILE})  # fmt: skip

    page = client_for(team).get(f"/team/catch-ups{ONE_DAY}").text

    [shown] = catch_ups(page)
    assert f'<a href="/repos/acme-web-shop/"><bdi>{ESCAPED}</bdi></a>' in shown
    assert f"<bdi>payments-api</bdi> (<bdi>{ESCAPED}</bdi>)" in shown
    assert re.search(r"<[^>]*\sonerror=", page) is None


def test_no_catch_ups_in_the_range(team: Team) -> None:
    record(team.paths, at(DAY, 10))
    later = DAY + timedelta(days=2)

    page = client_for(team).get(f"/team/catch-ups?from={later}&to={later}&calendar={later}").text

    assert "No catch-ups between these dates. Pick another day in the calendar." in page
    assert '<div class="calendar" dir="ltr">' in page and catch_ups(page) == []


def test_no_catch_ups_at_all(team: Team) -> None:
    page = client_for(team).get("/team/catch-ups").text

    assert "No catch-ups yet. Catching up from the team page fills this page." in page
    assert '<a class="button" href="/">' in page and 'class="calendar"' not in page
    assert not team_database(team.paths, "acme").exists()  # a page view creates no database
