"""The team's server: one login, the plain team page, and each guide under /repos/<target>/ (design section 24.7)."""

import functools
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant.estimate import UpdateEstimate
from codetrail.config import (
    GlobalConfig,
    HostingSettings,
    Paths,
    RemoteSettings,
    SignalSettings,
    TeamSource,
    write_remote_target,
)
from codetrail.errors import CodetrailError
from codetrail.guide import GuideRepository, Page
from codetrail.hosting import HostingService, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting
from codetrail.hosting.names import Service
from codetrail.lock import TargetBusy, target_lock
from codetrail.remove import remove_target, remove_team
from codetrail.repo.signal import Signal, behind
from codetrail.team.server import create_team_app, serve_team
from codetrail.team.settings import existing_target_names, save_selection, write_team
from codetrail.team.sync import sync_team
from codetrail.update import run_update
from codetrail.web.app import create_app, create_target_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_update_estimate import ESTIMATE
from tests.fixtures.catalogs import compiled_locales
from tests.fixtures.remote import allow_http_remotes, publish, serving_repository
from tests.fixtures.repos import make_repository
from tests.unit.test_signal import merge_feature
from tests.unit.test_team_follow import repository

ORIGIN = "http://127.0.0.1:8765"
SETTINGS = GlobalConfig(signal=SignalSettings(cache_seconds=0))  # every page reads the signals again
WRITTEN = Page("concepts/orders", {"kind": "concept", "title": "Orders"}, "How orders flow.")
TARGETS = {"acme-web-shop": ("R_1", "web-shop"), "acme-payments-api": ("R_2", "payments-api"),
           "acme-inventory": ("R_3", "inventory")}  # fmt: skip


@dataclass
class Team:
    paths: Paths
    url: str
    services: dict[Service, HostingService]
    held: list[str] = field(default_factory=list)
    released: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)  # target: why its lock can't be taken

    def hold(self, target: str) -> Callable[[], None]:
        """Records the target as held, as serve_team's lock would be, and returns its release."""
        if target in self.refused:
            raise TargetBusy(self.refused[target])
        self.held.append(target)
        return lambda: self.released.append(target)


def listing(*identifiers: str) -> dict[Service, HostingService]:
    """The fake service lists these ids; R_4 is a new repository with no push time, so it counts as active."""
    status = HostingStatus("github", installed=True, signed_in=True, account="reader", fix=None)
    listed = [repository(identifier, days_since_push=None if identifier == "R_4" else 1) for identifier in identifiers]
    return {"github": FakeHosting("github", [status], [Source("github", "acme-org", None)], {"acme-org": listed}, {})}


def add_target(team: Team, target: str, identifier: str, repository_name: str) -> None:
    remote = RemoteSettings(service="github", source="acme-org", id=identifier, name=repository_name, url=team.url)
    write_remote_target(team.paths, target, remote, "develop", "acme")


@contextmanager
def following_team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    """Team acme follows all of acme-org: web-shop is updated and one merge behind, payments-api was never updated,
    and inventory has left the source. Every target fetches from one repository served on 127.0.0.1, published from
    the checkout at `tmp_path / "checkout"`."""
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
        GuideRepository(paths.target_data("acme-web-shop") / "guide").write_page(WRITTEN)  # its guide has a page
        merge_feature(checkout, "one", {"services/api/orders.py": "y = 2\n"})
        publish(checkout, tmp_path / "served")
        yield team


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    with following_team(tmp_path, monkeypatch) as followed:
        yield followed


def app_for(team: Team, session: SessionState) -> object:
    """The team's app as serve_team builds it, after listing the sources once."""
    synced = sync_team(team.paths, "acme", team.services, datetime.now(UTC)) if team.services else None
    return create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.hold, synced=synced)


def client_for(team: Team, session: SessionState | None = None) -> TestClient:
    session = session or SessionState(60)
    client = TestClient(app_for(team, session), base_url=ORIGIN, follow_redirects=False)  # type: ignore[arg-type]
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def row(html: str, target: str) -> str:
    """The team page's card for one target, or its row: from its link to the next row."""
    match = re.search(rf'<article [^>]*data-team-card="{target}".*?</article>', html, re.DOTALL) or re.search(
        rf'<li[^>]*>\s*<a [^>]*href="/repos/{target}/".*?</li>', html, re.DOTALL
    )
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
    def unreachable(paths: Paths, name: str, since: str | None) -> Signal:
        if name == "acme-payments-api":
            raise CodetrailError("git couldn't fetch the repository. If it needs your sign-in, run: gh auth setup-git")
        return behind(paths, name, since)

    behind(team.paths, "acme-payments-api")  # cloned, so the page reads its signal
    monkeypatch.setattr("codetrail.team.server.behind", unreachable)

    page = client_for(team).get("/")

    assert page.status_code == 200
    repositories = section(page.text, "repositories-title")
    assert "1 merge landed" in row(repositories, "acme-web-shop")
    assert "If it needs your sign-in, run: gh auth setup-git" in row(repositories, "acme-payments-api")


def test_a_page_view_writes_nothing(team: Team) -> None:
    session = SessionState(60)
    synced = sync_team(team.paths, "acme", team.services, datetime.now(UTC))  # at the start
    services = listing("R_1", "R_2", "R_4")  # then a new repository becomes active
    app = create_team_app(team.paths, "acme", session, SETTINGS, services, team.hold, synced=synced)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    before = existing_target_names(team.paths)

    assert client.get("/").status_code == 200

    assert existing_target_names(team.paths) == before
    service = services["github"]
    assert isinstance(service, FakeHosting) and service.calls == []


def test_a_source_that_cannot_be_listed_says_so(team: Team) -> None:
    team.services = {"gitlab": listing()["github"]}  # no adapter for GitHub: the source can't be listed

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
    prefix = "/repos/acme-web-shop"
    with TestClient(app_for(team, session), base_url=ORIGIN, follow_redirects=False) as client:  # type: ignore[arg-type]  # leaving it stops the server
        assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
        started = client.post(f"{prefix}/update", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
        assert started.status_code == 202
        assert client.get(f"{prefix}/update/status").status_code == 403  # it holds the waiting estimate's id
        for _ in range(100):
            status = client.get(f"{prefix}/update/status", headers={TOKEN_HEADER: session.token})
            if status.json()["state"] == "waiting":
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
    client = TestClient(app_for(team, session), base_url=ORIGIN, follow_redirects=False)  # type: ignore[arg-type]

    response = client.get("/login", params={"code": session.issue_login_code(), "next": asked})

    assert response.status_code == 303
    assert response.headers["location"] == location


def test_a_used_sign_in_link_names_both_servers(team: Team) -> None:
    session = SessionState(60)
    client = TestClient(app_for(team, session), base_url=ORIGIN, follow_redirects=False)  # type: ignore[arg-type]
    code = session.issue_login_code()
    assert client.get(f"/login?code={code}").status_code == 303

    refused = client.get(f"/login?code={code}")

    assert refused.status_code == 403
    assert "codetrail serve" in refused.text and "codetrail team serve" in refused.text


def test_a_target_being_removed_shows_why(team: Team) -> None:
    team.refused["acme-payments-api"] = "Target 'acme-payments-api' is being removed."
    client = client_for(team)

    page = client.get("/").text

    refused = re.search(r'<li>\s*<span class="grow"><bdi>payments-api</bdi>.*?</li>', page, re.DOTALL)
    assert refused is not None and "is being removed" in refused.group(0)
    assert 'href="/repos/acme-payments-api/"' not in page
    assert client.get("/repos/acme-payments-api/").status_code == 404
    assert "1 merge landed" in row(section(page, "repositories-title"), "acme-web-shop")


def test_a_page_that_fails_to_build_releases_its_target(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(paths: Paths, name: str, *arguments: object, **keywords: object) -> object:
        if name == "acme-payments-api":
            raise RuntimeError("broken")
        return create_target_app(paths, name, *arguments, **keywords)  # type: ignore[arg-type]

    monkeypatch.setattr("codetrail.team.server.create_target_app", failing)

    with pytest.raises(RuntimeError):
        app_for(team, SessionState(60))

    assert "acme-payments-api" in team.held and team.released == ["acme-payments-api"]


def test_team_page_escapes_text_it_did_not_write(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    # Names are validated before they are stored, so the hostile text comes in through an unreachable target's reason.
    def hostile(paths: Paths, name: str, since: str | None) -> Signal:
        raise CodetrailError("git couldn't fetch <script>alert(1)</script>")

    monkeypatch.setattr("codetrail.team.server.behind", hostile)

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
    monkeypatch.setattr("codetrail.team.server.services_for", lambda settings: team.services)

    serve_team(team.paths, "acme", open_browser=False, start_path="/team/add")

    assert len(refusals) == 2 and all("in use" in refusal for refusal in refusals), refusals
    assert team.paths.target_file("acme-web-shop").exists() and team.paths.team_file("acme").exists()
    printed = capsys.readouterr().out
    assert re.search(r"http://127\.0\.0\.1:\d+/login\?code=[\w-]+&next=/team/add", printed), printed
    assert remove_target(team.paths, "acme-web-shop", lambda found: True)  # the server stopped: it can go


def test_serving_lists_the_sources_once_at_start(
    team: Team, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    team.services = listing("R_1", "R_2", "R_4")  # R_4 is new and active; inventory (R_3) has gone
    service = team.services["github"]
    assert isinstance(service, FakeHosting)
    pages: list[str] = []

    def run(app: object, *_arguments: object, **_keywords: object) -> None:
        code = re.search(r"/login\?code=([\w-]+)", capsys.readouterr().out)
        assert code is not None
        client = TestClient(app, base_url=ORIGIN, follow_redirects=False)  # type: ignore[arg-type]
        assert client.get(f"/login?code={code.group(1)}").status_code == 303
        pages.extend(client.get("/").text for _ in range(2))

    monkeypatch.setattr("codetrail.team.server.uvicorn.run", run)
    monkeypatch.setattr("codetrail.team.server.services_for", lambda settings: team.services)
    monkeypatch.setattr("codetrail.team.server.behind", lambda paths, name, since: Signal(updating=True))  # no fetch
    refreshed: list[str] = []
    finished = threading.Event()

    def refresh(paths: Paths, target: str, facts_only: bool) -> None:
        refreshed.append(target)
        finished.set()

    monkeypatch.setattr("codetrail.team.refresh.run_update", refresh)

    serve_team(team.paths, "acme", open_browser=False)

    assert "acme-repo-r-4" in existing_target_names(team.paths)  # written before serving
    assert finished.wait(10) and refreshed == ["acme-repo-r-4"]  # and given its free first refresh, as a save does
    assert service.calls == [("repositories", "acme-org")]  # once, not per page
    for page in pages:
        assert ">repo-R_4<" in row(section(page, "repositories-title"), "acme-repo-r-4")
        assert ">inventory<" in row(section(page, "stopped-title"), "acme-inventory")


def fetched_by_the_page(monkeypatch: pytest.MonkeyPatch, fetch: Callable[[Paths, str], Signal] = behind) -> list[str]:
    """The targets whose signal the team page reads (each read fetches into the target's mirror)."""
    fetched: list[str] = []

    def recorded(paths: Paths, name: str, since: str | None) -> Signal:
        fetched.append(name)
        return fetch(paths, name)

    monkeypatch.setattr("codetrail.team.server.behind", recorded)
    return fetched


def test_a_target_with_no_mirror_is_not_cloned_by_a_page_view(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = fetched_by_the_page(monkeypatch)

    page = client_for(team).get("/").text

    assert "Not updated yet" in row(section(page, "repositories-title"), "acme-payments-api")
    assert "acme-payments-api" not in fetched and "acme-web-shop" in fetched
    assert not (team.paths.target_data("acme-payments-api") / "mirror.git").exists()


def test_a_target_waiting_for_its_first_refresh_is_not_fetched_by_a_page_view(
    team: Team, monkeypatch: pytest.MonkeyPatch
) -> None:
    team.services = listing("R_1", "R_2", "R_4", "R_5")  # two new repositories, refreshed one at a time
    release = threading.Event()
    monkeypatch.setattr("codetrail.team.refresh.run_update", lambda paths, name, facts_only: release.wait(30))
    fetched = fetched_by_the_page(monkeypatch, lambda paths, name: Signal())  # nothing is fetched for real
    settings = SETTINGS.model_copy(update={"hosting": HostingSettings(concurrency=1)})
    session = SessionState(60)
    synced = sync_team(team.paths, "acme", team.services, datetime.now(UTC))
    app = create_team_app(team.paths, "acme", session, settings, team.services, team.hold, synced=synced)
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:  # leaving it stops the refresh
        try:
            assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
            repositories = section(client.get("/").text, "repositories-title")
            for target in ("acme-repo-r-4", "acme-repo-r-5"):  # one refreshing, one queued
                assert "Updating" in row(repositories, target), target
                assert target not in fetched
        finally:
            release.set()
        for _ in range(100):
            if client.get("/team/refresh/status", headers={TOKEN_HEADER: session.token}).json()["state"] == "done":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("the refresh never finished")
        repositories = section(client.get("/").text, "repositories-title")
    assert "Not updated yet" in row(repositories, "acme-repo-r-4")  # refreshed (no mirror here), so no longer pending
    assert "acme-repo-r-4" not in fetched and "acme-repo-r-5" not in fetched


def header(html: str) -> str:
    match = re.search(r'<header class="topbar">.*?</header>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


def sidebar(html: str) -> str:
    match = re.search(r'<nav class="sidebar".*?</nav>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


def test_team_page_uses_the_app_s_shell(team: Team) -> None:
    page = client_for(team).get("/").text

    assert '<div class="shell">' in page and '<main id="content" class="content"' in page
    assert "/static/js/main.js" in page
    top = header(page)
    assert re.search(r'<span class="target"[^>]*>\s*acme\s*</span>', top), top
    assert "data-theme-toggle" in top and "data-language-form" in top
    assert "data-search-form" not in top and "data-ask-open" not in top
    assert "data-ask-panel" not in page and "data-palette" not in page and "data-estimate-dialog" not in page
    side = sidebar(page)
    assert re.search(r'<a class="nav-link" href="/" aria-current="page">.*?Team</span></a>', side, re.DOTALL), side
    assert re.search(r'<a class="nav-link" href="/team/add">.*?Add repositories</span></a>', side, re.DOTALL)
    for target in TARGETS:  # every followed repository by its own name, the inventory too
        repository_name = TARGETS[target][1]
        assert re.search(rf'href="/repos/{target}/"[^>]*>.*?<bdi>{repository_name}</bdi>', side, re.DOTALL), target


def test_a_mounted_guide_links_back_to_its_team(team: Team) -> None:
    client = client_for(team)

    page = client.get("/repos/acme-web-shop/").text

    first = re.search(r'<ul class="nav-list">\s*<li>(.*?)</li>', sidebar(page), re.DOTALL)
    assert first is not None and 'href="/"' in first.group(1), page
    assert '<span class="label">← Team <bdi>acme</bdi></span>' in first.group(1)  # as its siblings, ellipsis and all
    assert re.search(r'<span class="target"[^>]*>web-shop<', header(page))  # the repository's name, not the target's
    assert "Part of team" not in page


def test_a_team_target_served_alone_points_to_its_team(team: Team) -> None:
    session = SessionState(60)
    client = TestClient(create_app(team.paths, "acme-web-shop", session, SETTINGS), base_url=ORIGIN)
    assert client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False).status_code == 303

    page = client.get("/").text

    assert "Part of team acme. Run <code>codetrail team serve acme</code> to see the whole team." in page
    assert "← Team" not in page
    assert re.search(r'<span class="target"[^>]*>web-shop<', header(page))


def test_the_team_s_language_can_be_switched(team: Team, tmp_path: Path) -> None:
    session = SessionState(60)
    locales = compiled_locales(tmp_path / "locales")
    app = create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.hold, locales)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    assert 'value="ar"' in client.get("/").text

    assert client.post("/settings/language", json={"language": "xx"}, headers=headers).status_code == 400
    assert client.post("/settings/language", json={"language": "ar"}, headers={"origin": ORIGIN}).status_code == 403
    assert client.post("/settings/language", json={"language": "ar"}, headers=headers).status_code == 204

    assert '<html lang="ar" dir="rtl">' in client.get("/").text
    assert '<html lang="ar" dir="rtl">' in client.get("/team/add").text


def test_a_target_with_facts_and_no_guide_says_so(team: Team) -> None:
    run_update(team.paths, "acme-payments-api", facts_only=True)  # its facts, and no page yet
    client = client_for(team)

    payments = row(section(client.get("/").text, "repositories-title"), "acme-payments-api")

    assert "No guide yet" in payments and "Up to date" not in payments
    assert re.search(r'<a class="stat-note" href="/repos/acme-payments-api/">Write its guide</a>', payments)
    for home in (client.get("/repos/acme-payments-api/").text, unmounted_home(team, "acme-payments-api")):
        update_card = home[home.index('id="update-title"') : home.index("</section>", home.index('id="update-title"'))]
        assert "No guide yet" in update_card and "Up to date" not in update_card
        assert "The guide matches the branch." not in update_card


def unmounted_home(team: Team, target: str) -> str:
    """The target's home as `codetrail serve` shows it, at `/`."""
    session = SessionState(60)
    client = TestClient(create_app(team.paths, target, session, SETTINGS), base_url=ORIGIN)
    assert client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False).status_code == 303
    page: str = client.get("/").text
    return page


@pytest.mark.parametrize(
    ("selection", "line"),
    [
        (TeamSource(service="github", source="acme-org"), "Every active repository<"),
        (
            TeamSource(service="github", source="acme-org", left_out=["R_9", "R_8"]),
            "Every active repository, 2 left out<",
        ),
        (TeamSource(service="github", source="acme-org", follow="chosen", chosen=["R_1"]), "Chosen repositories<"),
    ],
)
def test_each_source_says_how_it_is_followed(team: Team, selection: TeamSource, line: str) -> None:
    save_selection(team.paths, "acme", selection)

    assert line in client_for(team).get("/").text


def test_one_guide_that_cannot_be_read_does_not_stop_the_page(team: Team) -> None:
    broken = team.paths.target_data("acme-web-shop") / "guide" / "concepts" / "a-broken.md"  # read first
    broken.write_bytes(b"---\nkind: concept\ntitle: \xff\n---\n")  # not UTF-8: a half-written or hand-edited page
    run_update(team.paths, "acme-payments-api", facts_only=True)

    page = client_for(team).get("/")

    assert page.status_code == 200
    repositories = section(page.text, "repositories-title")
    web_shop = row(repositories, "acme-web-shop")
    assert "Couldn&#39;t read this repository&#39;s guide (UnicodeDecodeError)." in web_shop
    assert "No guide yet" in row(repositories, "acme-payments-api")
