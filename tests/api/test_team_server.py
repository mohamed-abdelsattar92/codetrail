"""The team's server: one login, the plain team page, and each guide under /repos/<target>/ (design section 24.7)."""

import functools
import re
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant.estimate import UpdateEstimate
from codetrail.config import GlobalConfig, Paths, RemoteSettings, SignalSettings, TeamSource, write_remote_target
from codetrail.errors import CodetrailError
from codetrail.hosting import HostingService, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting
from codetrail.hosting.names import Service
from codetrail.lock import target_lock
from codetrail.remove import remove_target, remove_team
from codetrail.repo.signal import Signal, behind
from codetrail.team.server import create_team_app, serve_team
from codetrail.team.settings import save_selection, write_team
from codetrail.update import run_update
from codetrail.web.app import create_target_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_update_estimate import ESTIMATE
from tests.fixtures.remote import allow_http_remotes, publish, serving_repository
from tests.fixtures.repos import make_repository
from tests.unit.test_signal import merge_feature
from tests.unit.test_team_follow import repository

ORIGIN = "http://127.0.0.1:8765"
SETTINGS = GlobalConfig(signal=SignalSettings(cache_seconds=0))  # every page reads the signals again
TARGETS = {"acme-web-shop": ("R_1", "web-shop"), "acme-payments-api": ("R_2", "payments-api"),
           "acme-inventory": ("R_3", "inventory")}  # fmt: skip


@dataclass
class Team:
    paths: Paths
    url: str
    services: dict[Service, HostingService]
    held: list[str] = field(default_factory=list)


def listing(*identifiers: str) -> dict[Service, HostingService]:
    status = HostingStatus("github", installed=True, signed_in=True, account="reader", fix=None)
    listed = [repository(identifier) for identifier in identifiers]
    return {"github": FakeHosting("github", [status], [Source("github", "acme-org", None)], {"acme-org": listed}, {})}


def add_target(team: Team, target: str, identifier: str, repository_name: str) -> None:
    remote = RemoteSettings(service="github", source="acme-org", id=identifier, name=repository_name, url=team.url)
    write_remote_target(team.paths, target, remote, "develop", "acme")


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    """Team acme follows all of acme-org: web-shop is updated and one merge behind, payments-api was never updated,
    and inventory has left the source. Every target fetches from one repository served on 127.0.0.1."""
    allow_http_remotes(monkeypatch, tmp_path)
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "checkout", [{"services/api/main.py": "x = 1\n", "README.md": "r\n"}])
    with ExitStack() as stack:
        url = stack.enter_context(serving_repository(checkout, tmp_path / "served"))
        write_team(paths, "acme")
        save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
        team = Team(paths, url, listing("R_1", "R_2"))
        for target, (identifier, repository_name) in TARGETS.items():
            add_target(team, target, identifier, repository_name)
        run_update(paths, "acme-web-shop", facts_only=True)
        merge_feature(checkout, "one", {"services/api/orders.py": "y = 2\n"})
        publish(checkout, tmp_path / "served")
        yield team


def client_for(team: Team, session: SessionState | None = None) -> TestClient:
    session = session or SessionState(60)
    app = create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.held.append)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def row(html: str, target: str) -> str:
    """The team page's row for one target: from its link to the next row."""
    match = re.search(rf'<li[^>]*>\s*<a [^>]*href="/repos/{target}/".*?</li>', html, re.DOTALL)
    assert match is not None, target
    return match.group(0)


def section(html: str, label: str) -> str:
    match = re.search(rf'<section[^>]*aria-labelledby="{label}".*?</section>', html, re.DOTALL)
    assert match is not None, label
    return match.group(0)


def test_team_page_lists_each_target_with_its_signal(team: Team) -> None:
    client = client_for(team)

    page = client.get("/")

    assert page.status_code == 200
    assert "<h1>acme</h1>" in page.text
    assert "GitHub" in page.text and "acme-org" in page.text
    repositories = section(page.text, "repositories-title")
    web_shop = row(repositories, "acme-web-shop")
    assert ">web-shop<" in web_shop and "develop" in web_shop
    assert "1 merge landed" in web_shop and "services" in web_shop
    assert "Not updated yet" in row(repositories, "acme-payments-api")
    assert "acme-inventory" not in repositories
    assert ">inventory<" in row(section(page.text, "stopped-title"), "acme-inventory")
    assert 'href="/team/add"' in page.text
    with target_lock(team.paths, "acme-web-shop"):  # an update runs
        updating = client.get("/").text
    assert "Updating" in row(section(updating, "repositories-title"), "acme-web-shop")
    assert sorted(team.held) == sorted(TARGETS)


def test_one_unreachable_repository_does_not_block_the_page(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(paths: Paths, name: str) -> Signal:
        if name == "acme-payments-api":
            raise CodetrailError("git couldn't fetch the repository. If it needs your sign-in, run: gh auth setup-git")
        return behind(paths, name)

    monkeypatch.setattr("codetrail.team.server.behind", unreachable)

    page = client_for(team).get("/")

    assert page.status_code == 200
    repositories = section(page.text, "repositories-title")
    assert "1 merge landed" in row(repositories, "acme-web-shop")
    assert "If it needs your sign-in, run: gh auth setup-git" in row(repositories, "acme-payments-api")


def test_a_source_that_cannot_be_listed_says_so(team: Team) -> None:
    team.services = {}  # no adapter: the source can't be listed, and nothing counts as gone

    page = client_for(team).get("/").text

    assert "couldn't be listed just now" in page
    assert "acme-inventory" in section(page, "repositories-title")


def test_each_guide_is_served_under_its_prefix(team: Team) -> None:
    client = client_for(team)

    for target in TARGETS:
        page = client.get(f"/repos/{target}/")
        assert page.status_code == 200, target
        assert f'<meta name="codetrail-base" content="/repos/{target}">' in page.text
        assert f'href="/repos/{target}/pages/' in page.text or f'href="/repos/{target}/progress"' in page.text
    assert client.get("/static/css/base.css").status_code == 200
    assert client.get("/repos/acme-elsewhere/").status_code == 404


def test_every_mounted_target_s_token_reads_need_the_token(team: Team) -> None:
    session = SessionState(60)
    client = client_for(team, session)
    add_target(team, "acme-docs-site", "R_4", "docs-site")  # followed once the app runs: mounted at the next page
    assert client.get("/").status_code == 200

    assert sorted(team.held) == sorted([*TARGETS, "acme-docs-site"])
    for target in team.held:
        for read in ("/bridge/answers", "/search/results?q=x"):
            assert client.get(f"/repos/{target}{read}").status_code == 403, (target, read)
        answers = client.get(f"/repos/{target}/bridge/answers", headers={TOKEN_HEADER: session.token})
        assert answers.status_code == 200, target


def test_a_left_out_repository_is_not_served(team: Team) -> None:
    save_selection(team.paths, "acme", TeamSource(service="github", source="acme-org", left_out=["R_2"]))

    client = client_for(team)

    assert "acme-payments-api" not in client.get("/").text
    assert client.get("/repos/acme-payments-api/").status_code == 404
    assert "acme-payments-api" not in team.held


def test_stopping_the_team_server_stops_each_guide_s_update(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    decisions: list[bool] = []

    def updater(confirm: Callable[[UpdateEstimate], bool], _progress: object) -> None:
        decisions.append(confirm(ESTIMATE))

    monkeypatch.setattr("codetrail.team.server.create_target_app", functools.partial(create_target_app,
                                                                                      updater=updater))  # fmt: skip
    session = SessionState(60)
    app = create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.held.append)
    prefix = "/repos/acme-web-shop"
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:  # leaving it stops the server
        assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
        started = client.post(f"{prefix}/update", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
        assert started.status_code == 202
        for _ in range(100):
            if client.get(f"{prefix}/update/status").json()["state"] == "waiting":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the update never showed its estimate")
    assert decisions == [False]  # the estimate was declined when the server stopped


@pytest.mark.parametrize(
    ("asked", "location"),
    [
        ("", "/"),
        ("/", "/"),
        ("/team/add", "/team/add"),
        ("/team/add/", "/"),
        ("//elsewhere.example/team/add", "/"),
        ("https://elsewhere.example/", "/"),
        ("/repos/acme-web-shop/", "/"),
        ("/team/add?x=1", "/"),
    ],
)
def test_login_next_is_limited_to_known_paths(team: Team, asked: str, location: str) -> None:
    session = SessionState(60)
    app = create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.held.append)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)

    response = client.get("/login", params={"code": session.issue_login_code(), "next": asked})

    assert response.status_code == 303
    assert response.headers["location"] == location


def test_team_page_escapes_names_from_the_service(team: Team) -> None:
    add_target(team, "acme-script", "R_9", "<script>alert(1)</script>")

    page = client_for(team).get("/").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert" not in page


def test_serving_holds_every_target(
    team: Team, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    refusals: list[str] = []

    def run(*_arguments: object, **_keywords: object) -> None:
        # Left out while the team is served: no longer followed, but still in use.
        save_selection(team.paths, "acme", TeamSource(service="github", source="acme-org", left_out=["R_1"]))
        removals: list[Callable[[], bool]] = [
            lambda: remove_target(team.paths, "acme-web-shop", lambda found: True),
            lambda: remove_team(team.paths, "acme", lambda found: True),
        ]
        for removal in removals:
            try:
                removal()
            except CodetrailError as error:
                refusals.append(str(error))

    monkeypatch.setattr("codetrail.team.server.uvicorn.run", run)

    serve_team(team.paths, "acme", open_browser=False, start_path="/team/add")

    assert len(refusals) == 2 and all("in use" in refusal for refusal in refusals), refusals
    assert team.paths.target_file("acme-web-shop").exists() and team.paths.team_file("acme").exists()
    printed = capsys.readouterr().out
    assert re.search(r"http://127\.0\.0\.1:\d+/login\?code=[\w-]+&next=/team/add", printed), printed
    assert remove_target(team.paths, "acme-web-shop", lambda found: True)  # the server stopped: it can go
