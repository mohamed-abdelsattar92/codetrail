"""The add-repositories screen's routes: the services' status, a source's repositories and saving a selection, which
writes the team and its new targets, mounts them and starts their free first refresh (design section 24.6)."""

import dataclasses
import re
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.config import Paths, RemoteSettings, TeamSource, load_target, load_team, write_remote_target
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, HostingError, HostingService, HostingStatus, Source
from codetrail.hosting.azure_devops import INSTALL as AZURE_INSTALL
from codetrail.hosting.fake import FakeHosting
from codetrail.hosting.names import Service
from codetrail.remove import remove_target
from codetrail.team.refresh import TeamRefreshJob
from codetrail.team.server import create_team_app
from codetrail.team.settings import existing_target_names, save_selection, write_team
from codetrail.team.sync import TeamSync
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_team_server import ORIGIN, SETTINGS, Team, add_target
from tests.fixtures.remote import allow_http_remotes, serving_repository
from tests.fixtures.repos import git, make_repository
from tests.unit.test_team_follow import repository

REPOSITORIES = {"R_1": ("web-shop", 1, False), "R_2": ("payments-api", 2, False), "R_3": ("inventory", 400, False),
                "R_4": ("old-site", 900, True)}  # fmt: skip  # id: (name, days since the last push, archived)
FETCH_FAILED = "git couldn't fetch the repository. If it needs your sign-in, run: gh auth setup-git"
READS = ("/team/hosting/sources", "/team/hosting/repositories?service=github&source=acme-org",
         "/team/hosting/branches?service=github&source=acme-org&id=R_1", "/team/refresh/status")  # fmt: skip


def hosted(url: str, identifier: str) -> HostedRepository:
    name, days, archived = REPOSITORIES[identifier]
    found = repository(identifier, archived=archived, default_branch="develop")
    pushed_at = datetime.now(UTC) - timedelta(days=days)  # the server reads activity against today
    return dataclasses.replace(found, name=name, https_url=url, pushed_at=pushed_at,
                               size_kb=2048 if identifier == "R_1" else None)  # fmt: skip


def services_for_test(url: str, *identifiers: str) -> dict[Service, HostingService]:
    """GitHub signed in, with acme-org listing these ids; Azure DevOps not installed; GitLab signed out."""
    listed = [hosted(url, identifier) for identifier in identifiers]
    return {
        "github": FakeHosting(
            "github", [HostingStatus("github", True, True, "reader", None)], [Source("github", "acme-org", None)],
            {"acme-org": listed}, {"R_1": ["develop", "release"]},
        ),
        "azure_devops": FakeHosting(
            "azure_devops", [HostingStatus("azure_devops", False, False, None, AZURE_INSTALL)],
            [Source("azure_devops", "contoso/Platform", None)], {}, {},
        ),
        "gitlab": FakeHosting(
            "gitlab", [HostingStatus("gitlab", True, False, None, "glab auth login")], [], {}, {},
        ),
    }  # fmt: skip


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    """Team acme follows nothing yet; acme-org lists two active repositories, a quiet one and an archived one, all
    fetched from one repository served on 127.0.0.1."""
    allow_http_remotes(monkeypatch, tmp_path)
    monkeypatch.setattr("codetrail.hosting.check_clone_url", lambda service, url: url)
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "checkout", [{"app/main.py": "x = 1\n", "README.md": "r\n"}])
    git(checkout, "branch", "release")
    with serving_repository(checkout, tmp_path / "served") as url:
        write_team(paths, "acme")
        yield Team(paths, url, services_for_test(url, *REPOSITORIES))


def signed_in(team: Team, session: SessionState) -> TestClient:
    app = create_team_app(team.paths, "acme", session, SETTINGS, team.services, team.hold)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    client.headers.update({TOKEN_HEADER: session.token, "origin": ORIGIN})
    return client


@pytest.fixture
def client(team: Team) -> Iterator[TestClient]:
    with signed_in(team, SessionState(60)) as client:  # leaving it stops the server, and the refresh with it
        yield client


def calls(team: Team, service: Service) -> list[tuple[str, ...]]:
    found = team.services[service]
    assert isinstance(found, FakeHosting)
    return found.calls


def finished(client: TestClient) -> dict[str, object]:
    for _ in range(400):
        status: dict[str, object] = client.get("/team/refresh/status").json()
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("the refresh never finished")


SELECTION: dict[str, object] = {"service": "github", "source": "acme-org", "follow": "all", "clone_over": "https",
                                "left_out": ["R_2"], "chosen": [], "branches": {"R_1": "release"}}  # fmt: skip


def test_the_screen_calls_no_service(team: Team, client: TestClient) -> None:
    page = client.get("/team/add")

    assert page.status_code == 200
    assert '<meta name="codetrail-token"' in page.text and "/static/js/team_add.js" in page.text
    assert "Follow the whole source" in page.text
    assert all(calls(team, service) == [] for service in team.services)


def test_the_screen_uses_the_app_s_shell(team: Team) -> None:
    save_selection(team.paths, "acme", TeamSource(service="github", source="acme-org"))
    add_target(team, "acme-web-shop", "R_1", "web-shop")  # followed, so served from the start

    with signed_in(team, SessionState(60)) as client:
        page = client.get("/team/add").text

    assert '<div class="shell">' in page and '<header class="topbar">' in page
    assert "data-theme-toggle" in page and "data-language-form" in page and "data-search-form" not in page
    nav = page[page.index('<nav class="sidebar"') : page.index("</nav>")]
    assert re.search(r'<a class="nav-link" href="/team/add" aria-current="page">', nav), nav
    assert re.search(r'href="/repos/acme-web-shop/"[^>]*>.*?<bdi>web-shop</bdi>', nav, re.DOTALL), nav
    assert page.index("/static/js/team_add.js") < page.index("/static/js/main.js")  # main.js marks the page ready


def test_the_screen_s_fields_are_labelled_once(team: Team, client: TestClient) -> None:
    page = client.get("/team/add").text

    source = page[page.index('aria-labelledby="source-title"') : page.index('aria-labelledby="list-title"')]
    assert source.count(">Source<") == 1  # the heading, which names the picker
    assert 'id="source-picker" aria-labelledby="source-title"' in source
    assert '<form class="field" data-organization-form hidden>' in source  # shown once Azure DevOps is signed in
    assert 'aria-label="Follow the whole source"' in page  # its visible label also counts what's left out
    assert '<label class="field" for="repository-filter">' in page


def test_sources_list_each_service_status_and_fix(team: Team, client: TestClient) -> None:
    listed = client.get("/team/hosting/sources").json()

    assert listed["services"] == [
        {"service": "github", "installed": True, "signed_in": True, "account": "reader", "fix": None,
         "sources_failed": False},
        {"service": "azure_devops", "installed": False, "signed_in": False, "account": None,
         "fix": AZURE_INSTALL, "sources_failed": False},
        {"service": "gitlab", "installed": True, "signed_in": False, "account": None, "fix": "glab auth login",
         "sources_failed": False},
    ]  # fmt: skip
    assert listed["sources"] == [{"service": "github", "name": "acme-org", "repository_count": None}]
    assert ("sources", "") in calls(team, "github")
    assert not any(call[0] == "sources" for call in calls(team, "gitlab"))  # signed out: nothing to list


def test_an_azure_organization_is_named_and_checked(team: Team, client: TestClient) -> None:
    team.services["azure_devops"] = FakeHosting(
        "azure_devops", [HostingStatus("azure_devops", True, True, "reader", None)],
        [Source("azure_devops", "contoso/Platform", None)], {}, {},
    )  # fmt: skip

    listed = client.get("/team/hosting/sources", params={"organization": "contoso"}).json()

    assert {"service": "azure_devops", "name": "contoso/Platform", "repository_count": None} in listed["sources"]
    assert ("sources", "contoso") in calls(team, "azure_devops")
    assert {"service": "github", "name": "acme-org", "repository_count": None} in listed["sources"]  # it ignores it
    for organization in ("-contoso", "contoso/Platform", "con toso", "x" * 60):
        refused = client.get("/team/hosting/sources", params={"organization": organization})
        assert refused.status_code == 422, organization


def test_repositories_are_listed_with_activity(team: Team, client: TestClient) -> None:
    listed = client.get("/team/hosting/repositories", params={"service": "github", "source": "acme-org"}).json()

    by_id = {found["id"]: found for found in listed["repositories"]}
    assert by_id["R_1"] == {
        "id": "R_1", "name": "web-shop", "default_branch": "develop", "branch": "develop", "archived": False,
        "pushed_at": by_id["R_1"]["pushed_at"], "size_kb": 2048, "active": True, "followed": False, "gone": False,
    }  # fmt: skip
    assert by_id["R_1"]["pushed_at"].endswith("+00:00")
    assert [by_id[identifier]["active"] for identifier in ("R_2", "R_3")] == [True, False]
    assert by_id["R_4"]["archived"] is True
    assert listed["following"] is None
    remote = RemoteSettings(service="github", source="acme-org", id="R_2", name="payments-api", url=team.url)
    write_remote_target(team.paths, "acme-payments-api", remote, "release", "acme")  # taught on another branch
    followed = client.get("/team/hosting/repositories", params={"service": "github", "source": "acme-org"}).json()
    payments = next(found for found in followed["repositories"] if found["id"] == "R_2")
    assert (payments["followed"], payments["branch"], payments["default_branch"]) == (True, "release", "develop")
    save_selection(team.paths, "acme", TeamSource(service="github", source="acme-org", left_out=["R_2"]))
    following = client.get("/team/hosting/repositories", params={"service": "github", "source": "acme-org"}).json()
    assert following["following"] == {"follow": "all", "left_out": ["R_2"], "chosen": [], "clone_over": "https"}


def test_a_source_that_fails_its_checks_is_refused(team: Team, client: TestClient) -> None:
    for query in ({"service": "github", "source": "acme org"}, {"service": "bitbucket", "source": "acme-org"},
                  {"service": "github", "source": "-acme"}):  # fmt: skip
        assert client.get("/team/hosting/repositories", params=query).status_code == 422, query
    assert calls(team, "github") == []


def test_branches_are_listed_for_a_listed_repository(team: Team, client: TestClient) -> None:
    query = {"service": "github", "source": "acme-org"}

    assert client.get("/team/hosting/branches", params={**query, "id": "R_1"}).json() == {
        "branches": ["develop", "release"]
    }
    assert client.get("/team/hosting/branches", params={**query, "id": "R_9"}).status_code == 422
    assert client.get("/team/hosting/branches", params={**query, "id": "-R_1"}).status_code == 422
    assert ("branches", "R_1") in calls(team, "github") and ("branches", "R_9") not in calls(team, "github")


def test_hosting_reads_need_the_token(team: Team, client: TestClient) -> None:
    for read in READS:
        assert client.get(read, headers={TOKEN_HEADER: ""}).status_code == 403, read
        assert client.get(read).status_code == 200, read


def test_saving_needs_the_origin_and_the_token(team: Team, client: TestClient) -> None:
    for headers in ({"origin": "http://elsewhere.example"}, {TOKEN_HEADER: "wrong"}):
        assert client.post("/team/repositories", json=SELECTION, headers=headers).status_code == 403
    assert load_team(team.paths, "acme").sources == []


@pytest.mark.parametrize(
    "change",
    [
        {"chosen": ["R_9"], "follow": "chosen"},  # not listed
        {"left_out": ["R_9"]},
        {"left_out": ["-R_1"]},
        {"branches": {"R_9": "develop"}},
        {"branches": {"R_1": "--upload-pack=x"}},
        {"branches": {"R_1": "-x"}},
        {"branches": {"R_1": "two..dots"}},
        {"branches": {"R_1": "not-on-the-service"}},
        {"extra": True},
        {"service": "bitbucket"},
        {"source": "acme org"},
        {"follow": "some"},
        {"clone_over": "file"},
        {"left_out": [f"R_{number}" for number in range(1001)]},  # more than max_repositories_per_source
    ],
)
def test_unknown_ids_and_bad_branches_are_refused(team: Team, client: TestClient, change: dict[str, object]) -> None:
    team_file = team.paths.team_file("acme").read_text()

    response = client.post("/team/repositories", json={**SELECTION, **change})

    assert response.status_code in (400, 422), response.text
    assert team.paths.team_file("acme").read_text() == team_file
    assert existing_target_names(team.paths) == []
    assert client.get("/team/refresh/status").json()["state"] == "idle"


def test_saving_writes_team_and_targets_and_starts_the_refresh(team: Team, client: TestClient) -> None:
    saved = client.post("/team/repositories", json=SELECTION)

    assert saved.status_code == 200, saved.text
    assert saved.json() == {"added": ["acme-web-shop"], "refresh": True}  # inventory is quiet, old-site archived
    source = load_team(team.paths, "acme").sources[0]
    assert (source.follow, source.left_out, source.chosen) == ("all", ["R_2"], [])
    target = load_target(team.paths, "acme-web-shop")
    assert target.branch == "release" and target.team == "acme"
    assert target.remote is not None and target.remote.id == "R_1"
    assert team.held == ["acme-web-shop"]
    status = finished(client)
    assert status["state"] == "done"
    assert status["steps"] == [{"step": "started", "target": "acme-web-shop", "repository": "web-shop"},
                               {"step": "done", "target": "acme-web-shop", "repository": "web-shop"}]  # fmt: skip
    assert (team.paths.target_data("acme-web-shop") / "codetrail.db").exists()  # cloned, scanned and its facts read
    assert client.get("/team/refresh/status", params={"after": status["next"]}).json()["steps"] == []
    guide = client.get("/repos/acme-web-shop/")
    assert guide.status_code == 200
    assert ">web-shop<" in client.get("/").text


def test_hand_picked_repositories_are_followed(team: Team, client: TestClient) -> None:
    chosen = {**SELECTION, "follow": "chosen", "left_out": [], "chosen": ["R_2", "R_3"], "branches": {}}

    assert client.post("/team/repositories", json=chosen).json()["added"] == ["acme-payments-api", "acme-inventory"]
    finished(client)

    assert load_team(team.paths, "acme").sources[0].chosen == ["R_2", "R_3"]


def test_a_second_save_while_the_refresh_runs_is_refused(
    team: Team, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr("codetrail.team.refresh.run_update", lambda paths, name, facts_only: release.wait(30))
    assert client.post("/team/repositories", json=SELECTION).status_code == 200

    again = client.post("/team/repositories", json={**SELECTION, "left_out": []})

    assert again.status_code == 409
    assert load_team(team.paths, "acme").sources[0].left_out == ["R_2"]
    release.set()
    assert finished(client)["state"] == "done"


def test_a_failed_refresh_says_why(team: Team, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(paths: Paths, name: str, facts_only: bool) -> None:
        raise CodetrailError(FETCH_FAILED)

    monkeypatch.setattr("codetrail.team.refresh.run_update", failing)
    client.post("/team/repositories", json=SELECTION)

    steps = finished(client)["steps"]

    assert steps == [
        {"step": "started", "target": "acme-web-shop", "repository": "web-shop"},
        {"step": "failed", "target": "acme-web-shop", "repository": "web-shop", "message": FETCH_FAILED},
    ]


def test_a_repository_gone_from_the_service_can_be_left_out_then_removed(team: Team, client: TestClient) -> None:
    save_selection(team.paths, "acme", TeamSource(service="github", source="acme-org"))
    add_target(team, "acme-legacy", "R_7", "legacy")  # followed, but the service no longer lists it
    listed = client.get("/team/hosting/repositories", params={"service": "github", "source": "acme-org"}).json()
    gone = next(found for found in listed["repositories"] if found["id"] == "R_7")
    assert (gone["name"], gone["gone"], gone["followed"]) == ("legacy", True, True)
    with pytest.raises(CodetrailError, match="leave it out"):
        remove_target(team.paths, "acme-legacy", lambda found: True)

    saved = client.post("/team/repositories", json={**SELECTION, "left_out": ["R_2", "R_7"], "branches": {}})

    assert saved.status_code == 200, saved.text
    assert remove_target(team.paths, "acme-legacy", lambda found: True)


def test_stopping_the_server_lets_no_further_refresh_start(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    entered, release = threading.Event(), threading.Event()
    refreshed: list[str] = []

    def slow(paths: Paths, name: str, facts_only: bool) -> None:
        refreshed.append(name)
        entered.set()
        release.wait(30)

    monkeypatch.setattr("codetrail.team.refresh.run_update", slow)
    job = TeamRefreshJob(team.paths, concurrency=1)
    assert job.start(["acme-a", "acme-b", "acme-c"])
    assert entered.wait(10)
    job.request_stop()  # as the server's lifespan does
    stopper = threading.Thread(target=job.wait_for_stop)
    stopper.start()
    time.sleep(0.1)
    assert stopper.is_alive()  # it waits for the refresh that runs
    release.set()
    stopper.join(10)

    assert refreshed == ["acme-a"]
    assert not job.start(["acme-d"])  # stopped for good


def test_a_source_listing_that_fails_is_reported_per_service(
    team: Team, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(organization: str | None = None) -> list[Source]:
        raise HostingError("gh couldn't list the organizations.")

    monkeypatch.setattr(team.services["github"], "sources", failing)

    listed = client.get("/team/hosting/sources").json()

    assert [status["sources_failed"] for status in listed["services"]] == [True, False, False]
    assert listed["sources"] == []


def test_a_settings_error_comes_back_as_a_message(team: Team, client: TestClient) -> None:
    team.paths.team_file("acme").write_text("sources = 'not a list'\n")

    response = client.get("/team/hosting/repositories", params={"service": "github", "source": "acme-org"})

    assert response.status_code == 500
    assert "acme.toml" in response.json()["error"]


def test_earlier_left_out_ids_the_screen_does_not_show_are_kept(team: Team, client: TestClient) -> None:
    frozen = dataclasses.replace(hosted(team.url, "R_3"), id="R_8", name="frozen", disabled=True)
    service = team.services["github"]
    assert isinstance(service, FakeHosting)
    service._repositories["acme-org"].append(frozen)  # disabled: never shown, never followed
    save_selection(team.paths, "acme", TeamSource(service="github", source="acme-org", left_out=["R_1", "R_8", "R_9"]))

    saved = client.post("/team/repositories", json={**SELECTION, "left_out": ["R_2"], "branches": {}})

    assert saved.status_code == 200, saved.text
    # R_1 was shown and ticked again; R_8 (disabled) and R_9 (no longer listed) weren't on the screen.
    assert load_team(team.paths, "acme").sources[0].left_out == ["R_2", "R_8", "R_9"]
    finished(client)


def test_a_save_while_another_is_saving_is_refused(
    team: Team, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()

    def slow_sync(*arguments: object, **keywords: object) -> TeamSync:
        entered.set()
        release.wait(30)
        return TeamSync()

    monkeypatch.setattr("codetrail.team.routes.sync_team", slow_sync)
    first = threading.Thread(target=client.post, args=("/team/repositories",), kwargs={"json": SELECTION})
    first.start()
    assert entered.wait(10)

    again = client.post("/team/repositories", json=SELECTION)

    release.set()
    first.join(10)
    assert again.status_code == 409
    assert again.json()["error"] == "Another save is in progress."


def test_a_save_while_the_server_stops_says_the_refresh_did_not_start(
    team: Team, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("codetrail.team.refresh.TeamRefreshJob.start", lambda job, targets: False)

    saved = client.post("/team/repositories", json=SELECTION)

    assert saved.json() == {"added": ["acme-web-shop"], "refresh": False}
