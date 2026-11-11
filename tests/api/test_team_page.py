"""The team page's header, its "Across the team" overview and its cards (design section 24.7).

Team acme follows web-shop, updated with a guide and one merge behind, and payments-api, never updated; inventory has
left the source. A page view reads what's there and spends nothing: no assistant call, no source listed.
"""

import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant.estimate import when_text
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.database import connect
from codetrail.guide import GuideRepository, Page
from codetrail.hosting.fake import FakeHosting
from codetrail.team import follow, server
from codetrail.team.state import Overviews, TeamState, team_database
from codetrail.update import run_update
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_team_server import ORIGIN, SETTINGS, Team, client_for, following_team, row, section
from tests.fixtures.remote import publish
from tests.fixtures.repos import git

OVERVIEW = "overviews/2026-11-11-101500"
DIGEST = "digests/2026-11-11-abc123"
HOSTILE = "<img src=x onerror=alert(1)>"
AUTHOR = 'Eve" onerror="alert(1)'


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    with following_team(tmp_path, monkeypatch) as followed:
        yield followed


def finish_catch_up(
    paths: Paths,
    overview_id: str | None = None,
    problem: str | None = None,
    failed: Mapping[str, str] | None = None,
) -> str:
    """Records a finished catch-up in the team's database, and returns when it finished."""
    connection = connect(team_database(paths, "acme"))
    try:
        state = TeamState(connection)
        state.finish(state.start(), ["acme-web-shop"], overview_id, problem, failed or {})
        record = state.last_finished()
    finally:
        connection.close()
    assert record is not None
    return record.finished_at


def write_overview(paths: Paths) -> None:
    meta: dict[str, object] = {
        "kind": "overview",
        "digests": [{"target": "acme-web-shop", "repository": "web-shop", "id": DIGEST}],
        "cited": ["acme-web-shop"],
    }
    body = (
        'Both services now retry charges.\n\n> [!documented] services/api/main.py#L1\n> "x = 1"\n\n'
        f"See [the digest](/repos/acme-web-shop/pages/{DIGEST})."
    )
    Overviews(paths.team_data("acme") / "overviews").write(OVERVIEW, meta, body)


def write_digest(paths: Paths, title: str = "Charges retry twice", highlights: list[str] | None = None) -> None:
    meta: dict[str, object] = {"kind": "digest", "title": title, "from_commit": None, "to_commit": "b" * 40,
                               "written_at": "2026-11-11T10:15:00+00:00", "pages_changed": ["concepts/orders"],
                               "highlights": highlights or ["Charges retry **twice** now."]}  # fmt: skip
    GuideRepository(paths.target_data("acme-web-shop") / "guide").write_page(Page(DIGEST, meta, "Things changed."))


def caught_up(team: Team) -> None:
    """web-shop's facts are refreshed to its branch's head: nothing is new but its digest."""
    run_update(team.paths, "acme-web-shop", facts_only=True)


def post(client: TestClient, path: str, body: object, token: str) -> int:
    status: int = client.post(path, json=body, headers={"origin": ORIGIN, TOKEN_HEADER: token}).status_code
    return status


def header(html: str) -> str:
    match = re.search(r'<header class="page-head team-head">.*?</header>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


def test_header_never_caught_up(team: Team) -> None:
    page = client_for(team).get("/").text

    head = header(page)
    assert "You haven't caught up yet." in head
    assert "1 of 2 repositories has new merges" in head
    catch_up, add = head.index("data-catch-up-button"), head.index('href="/team/add"')
    assert catch_up < add < head.index("acme-org")  # Catch up, then Add repositories, then the sources
    assert 'class="button primary" data-catch-up-button' in head
    assert not team_database(team.paths, "acme").exists()  # a page view creates no database


def test_header_last_catch_up_time(team: Team) -> None:
    finished_at = finish_catch_up(team.paths)

    head = header(client_for(team).get("/").text)

    assert f'Since you last caught up: <time datetime="{finished_at}" dir="ltr">{when_text(finished_at)}</time>' in head
    assert "haven't caught up" not in head


def test_overview_shown_until_read(team: Team) -> None:
    write_overview(team.paths)
    finish_catch_up(team.paths, OVERVIEW)
    session = SessionState(60)
    client = client_for(team, session)

    overview = section(client.get("/").text, "overview-title")

    assert "Across the team" in overview and "Both services now retry charges." in overview
    assert 'lang="en" dir="ltr"' in overview
    assert 'href="/repos/acme-web-shop/source/services/api/main.py#L1"' in overview  # its cited repository's source
    assert f'From the digests of <a href="/repos/acme-web-shop/pages/{DIGEST}"><bdi>web-shop</bdi></a>' in overview
    assert f'data-overview-read="{OVERVIEW}"' in overview
    without_token = client.post("/team/overview/read", json={"overview_id": OVERVIEW}, headers={"origin": ORIGIN})
    assert without_token.status_code == 403

    assert post(client, "/team/overview/read", {"overview_id": OVERVIEW}, session.token) == 204

    page = client.get("/").text
    assert "overview-title" not in page and "Both services now retry charges." not in page


ARABIC_INDIC = OVERVIEW.translate({ord(digit): 0x0660 + int(digit) for digit in "0123456789"})


@pytest.mark.parametrize(
    "overview_id",
    ["overviews/2026-01-01-000000", "../x", "overviews/../../x", ARABIC_INDIC, "digests/2026-11-11-a"],
)
def test_overview_read_unknown_404(team: Team, overview_id: str) -> None:
    write_overview(team.paths)
    session = SessionState(60)
    client = client_for(team, session)

    assert post(client, "/team/overview/read", {"overview_id": overview_id}, session.token) == 404


def test_overview_read_takes_bounded_json_only(team: Team) -> None:
    write_overview(team.paths)
    session = SessionState(60)
    client = client_for(team, session)

    assert post(client, "/team/overview/read", {"overview_id": OVERVIEW, "also": 1}, session.token) == 422
    assert post(client, "/team/overview/read", {"overview_id": "x" * 101}, session.token) == 422


def test_overview_problem_shown(team: Team) -> None:
    finish_catch_up(team.paths, problem="The overview failed its checks twice: <b>too long</b>")

    overview = section(client_for(team).get("/").text, "overview-title")

    problem = "The overview failed its checks twice: &lt;b&gt;too long&lt;/b&gt;"
    assert f"This catch-up has no overview: {problem}" in overview
    assert "data-overview-read" not in overview


def test_a_skipped_overview_has_no_section(team: Team) -> None:
    finish_catch_up(team.paths)

    assert "overview-title" not in client_for(team).get("/").text


def test_card_not_caught_up_line(team: Team) -> None:
    finish_catch_up(team.paths, failed={"acme-web-shop": "Target 'acme-web-shop' is <busy>."})

    page = client_for(team).get("/").text

    card = row(section(page, "repositories-title"), "acme-web-shop")
    assert card.startswith('<article class="team-card not-caught-up"')
    assert 'Not caught up yet: 1 merge touched <span lang="en" dir="ltr">services</span>.' in card
    assert "Not caught up in the last catch-up: Target &#39;acme-web-shop&#39; is &lt;busy&gt;." in card
    assert '<h3 class="team-card-title"><a href="/repos/acme-web-shop/"><bdi>web-shop</bdi></a></h3>' in card
    assert "Merged by: <bdi>Test Author</bdi>" in card


def test_card_shows_its_unread_digest(team: Team) -> None:
    documented = '> [!documented] services/api/main.py#L1\n> "x = 1"'
    write_digest(team.paths, highlights=["Charges retry **twice** now.", documented])

    card = row(client_for(team).get("/").text, "acme-web-shop")

    assert f'href="/repos/acme-web-shop/pages/{DIGEST}"' in card
    assert "<strong>twice</strong>" in card
    assert 'href="/repos/acme-web-shop/source/services/api/main.py#L1"' in card
    assert "1 guide page changed" in card
    assert f'data-digest-read="{DIGEST}"' in card and 'data-target="acme-web-shop"' in card


def test_card_names_escaped(team: Team, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    followed = follow.followed_targets

    def hostile_names(*arguments: object) -> dict[str, tuple[str, str]]:
        targets = followed(*arguments)  # type: ignore[arg-type]
        return {target: (HOSTILE if target == "acme-web-shop" else repository, branch)
                for target, (repository, branch) in targets.items()}  # fmt: skip

    monkeypatch.setattr("codetrail.team.server.followed_targets", hostile_names)
    checkout = tmp_path / "checkout"  # git keeps no < or > in a name, so the author breaks out of an attribute instead
    git(checkout, "commit", "-q", "--allow-empty", "--author", f"{AUTHOR} <x@example.com>", "-m", "Work")
    publish(checkout, tmp_path / "served")
    write_digest(team.paths, title="{{7*7}} " + HOSTILE)

    page = client_for(team).get("/").text

    card = row(page, "acme-web-shop")
    escaped = "&lt;img src=x onerror=alert(1)&gt;"
    assert f'<a href="/repos/acme-web-shop/"><bdi>{escaped}</bdi></a>' in card
    assert "Merged by: <bdi>Eve&#34; onerror=&#34;alert(1)</bdi>, <bdi>Test Author</bdi>" in card
    assert f"<bdi>{{{{7*7}}}} {escaped}</bdi>" in card and ">49<" not in card
    assert re.search(r"<[^>]*\sonerror=", page) is None


def test_card_mark_read_reuses_learn_route(team: Team) -> None:
    caught_up(team)
    write_digest(team.paths)
    session = SessionState(60)
    client = client_for(team, session)
    card = row(client.get("/").text, "acme-web-shop")
    assert card.startswith('<article class="team-card"')  # caught up: no dashed edge

    assert post(client, "/repos/acme-web-shop/learn/read", {"page_id": DIGEST}, session.token) == 204

    page = client.get("/").text
    assert 'data-team-card="acme-web-shop"' not in page
    assert re.search(r'Nothing new: <a href="/repos/acme-web-shop/"><bdi>web-shop</bdi></a>', page)


def test_team_page_spends_nothing(team: Team) -> None:
    write_overview(team.paths)
    finish_catch_up(team.paths, OVERVIEW)
    write_digest(team.paths)
    writer, overview_writer = FakeAssistant(), FakeAssistant()
    session = SessionState(60)
    app = server.create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.hold,
                                 writer_for=lambda target: writer, overview_writer=overview_writer)  # fmt: skip
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303

    page = client.get("/")

    assert page.status_code == 200 and "Across the team" in page.text and 'data-team-card="acme-web-shop"' in page.text
    assert writer.requests == [] and overview_writer.requests == []
    service = team.services["github"]
    assert isinstance(service, FakeHosting) and service.calls == []
