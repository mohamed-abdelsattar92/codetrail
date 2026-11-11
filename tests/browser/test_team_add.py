"""The team server in a real browser: the add-repositories screen against the fake hosting service, and a guide
mounted under /repos/<target>/ (design sections 24.6 and 24.7).

The team app runs as `codetrail team serve` builds it, on 127.0.0.1, behind the same security middleware; every test
signs in through the real single-use link. The fake service's repositories are fetched from one repository served on
127.0.0.1, so saving runs the real free first refresh.
"""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from codetrail.config import (
    GlobalConfig,
    Paths,
    RemoteSettings,
    TeamSource,
    load_target,
    load_team,
    write_remote_target,
)
from codetrail.hosting import HostingError, HostingService, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting
from codetrail.hosting.names import Service
from codetrail.team.server import create_team_app
from codetrail.team.settings import existing_target_names, save_selection, write_team
from codetrail.update import run_update
from codetrail.web.security import SessionState
from tests.api.test_team_add import REPOSITORIES, services_for_test
from tests.browser.conftest import FILES, free_port, ready
from tests.browser.test_accessibility import AXE
from tests.fixtures.remote import allow_http_remotes, serving_repository, write_served_target
from tests.fixtures.repos import git, make_repository

pytestmark = pytest.mark.browser
NOTHING_NEW = "Saved. There's no new repository to get ready."
FINISHED = "Done. Open a repository and use Update the guide to write its guide; you'll see the estimate first."
REFRESH_SECONDS = 60_000  # a first refresh clones and reads the facts


@dataclass
class TeamSite:
    url: str
    session: SessionState
    paths: Paths
    repository_url: str
    services: dict[Service, HostingService]

    def sign_in(self, page: Page, path: str = "/team/add") -> None:
        page.goto(f"{self.url}/login?code={self.session.issue_login_code()}&next={path}")
        if path == "/team/add":
            ready(page)

    def refresh(self, target: str) -> None:
        # Playwright keeps an event loop running on this thread, so the update gets its own.
        updating = threading.Thread(target=run_update, args=(self.paths, target), kwargs={"facts_only": True})
        updating.start()
        updating.join()


@pytest.fixture
def team_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TeamSite]:
    allow_http_remotes(monkeypatch, tmp_path)
    monkeypatch.setattr("codetrail.hosting.check_clone_url", lambda service, url: url)
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "checkout", [FILES])
    git(checkout, "branch", "release")
    with serving_repository(checkout, tmp_path / "served") as repository_url:
        write_team(paths, "acme")
        port = free_port()
        settings = GlobalConfig.model_validate({"server": {"port": port}})
        session = SessionState(60)
        hold: Callable[[str], Callable[[], None]] = lambda target: lambda: None  # noqa: E731 - no lock to take here
        services = services_for_test(repository_url, *REPOSITORIES)
        app = create_team_app(paths, "acme", session, settings, services, hold)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        assert server.started and thread.is_alive(), "The test server didn't start."  # nothing goes to another one
        yield TeamSite(f"http://127.0.0.1:{port}", session, paths, repository_url, services)
        server.should_exit = True
        thread.join(30)


def choose_the_source(page: Page) -> None:
    page.locator("[data-source-picker]").select_option(label="acme-org")
    expect(page.locator("[data-list]")).to_be_visible()


def test_follow_all_then_leave_one_out(page: Page, team_site: TeamSite, errors: list[str]) -> None:
    team_site.sign_in(page)
    services = page.locator("[data-services] li")
    expect(services).to_have_count(3)
    expect(services.nth(0)).to_contain_text("Signed in as reader")
    expect(services.nth(2).locator("code")).to_have_text("glab auth login")
    install = services.nth(1).get_by_role("link", name="https://learn.microsoft.com/cli/azure/install-azure-cli")
    expect(install).to_have_attribute("href", "https://learn.microsoft.com/cli/azure/install-azure-cli")
    expect(install).to_have_attribute("rel", "noreferrer")
    expect(install).to_have_attribute("target", "_blank")
    expect(services.nth(1).locator("code")).to_have_text("az extension add --name azure-devops")
    expect(page.locator("[data-organization-form]")).to_be_hidden()  # Azure DevOps isn't installed
    choose_the_source(page)
    # The quiet inventory and the archived old-site are filtered out; the active two are ticked.
    expect(page.locator("[data-rows] > li")).to_have_count(2)
    expect(page.locator("[data-follow-all]")).to_be_checked()
    expect(page.locator("[data-save]")).to_have_text("Add 2 repositories")

    page.get_by_label("payments-api", exact=True).uncheck()

    expect(page.locator("[data-follow-label]")).to_have_text("Follow the whole source (1 repository left out)")
    expect(page.locator("[data-save]")).to_have_text("Add 1 repository")
    page.locator("[data-save]").click()
    expect(page.locator("[data-refresh-state]")).to_have_text(FINISHED, timeout=REFRESH_SECONDS)
    expect(page.locator("[data-refresh-log] li.done")).to_have_text(["web-shop is ready."])
    # The source's repositories are listed again: web-shop is followed now, so saving adds nothing.
    expect(page.get_by_label("Branch for web-shop")).to_be_disabled()
    expect(page.locator("[data-save]")).to_have_text("Save")
    source = load_team(team_site.paths, "acme").sources[0]
    assert (source.follow, source.left_out) == ("all", ["R_2"])
    assert existing_target_names(team_site.paths) == ["acme-web-shop"]
    page.get_by_role("link", name="Go to the team page").click()
    expect(page.locator("#content a[href='/repos/acme-web-shop/']")).to_have_text("web-shop")
    assert errors == []


def test_switch_off_keeps_the_ticked_rows(page: Page, team_site: TeamSite, errors: list[str]) -> None:
    team_site.sign_in(page)
    choose_the_source(page)
    page.get_by_label("payments-api", exact=True).uncheck()

    page.locator("[data-follow-all]").uncheck()

    expect(page.get_by_label("web-shop", exact=True)).to_be_checked()
    expect(page.get_by_label("payments-api", exact=True)).not_to_be_checked()
    expect(page.locator("[data-follow-label]")).to_have_text("Follow the whole source")
    page.locator("[data-active-only]").uncheck()  # the quiet one can be picked by hand
    page.get_by_label("inventory", exact=True).check()
    expect(page.locator("[data-save]")).to_have_text("Add 2 repositories")
    page.locator("[data-save]").click()
    expect(page.locator("[data-refresh-state]")).to_have_text(FINISHED, timeout=REFRESH_SECONDS)
    source = load_team(team_site.paths, "acme").sources[0]
    assert (source.follow, sorted(source.chosen), source.left_out) == ("chosen", ["R_1", "R_3"], [])
    assert existing_target_names(team_site.paths) == ["acme-inventory", "acme-web-shop"]
    assert errors == []


def test_branch_picker_loads_and_saves(page: Page, team_site: TeamSite, errors: list[str]) -> None:
    team_site.sign_in(page)
    choose_the_source(page)
    branch = page.get_by_label("Branch for web-shop")
    expect(branch.locator("option")).to_have_text(["develop"])

    branch.focus()  # opening it lists the repository's branches

    expect(branch.locator("option")).to_have_text(["develop", "release"])
    branch.select_option("release")
    page.locator("[data-save]").click()
    expect(page.locator("[data-refresh-state]")).to_have_text(FINISHED, timeout=REFRESH_SECONDS)
    assert load_target(team_site.paths, "acme-web-shop").branch == "release"
    assert load_target(team_site.paths, "acme-payments-api").branch == "develop"
    assert errors == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_add_screen_has_no_accessibility_violations(
    page: Page, team_site: TeamSite, errors: list[str], scheme: str
) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    team_site.sign_in(page)
    choose_the_source(page)
    page.locator("[data-active-only]").uncheck()
    page.locator("[data-show-archived]").check()
    expect(page.locator("[data-rows] > li")).to_have_count(4)
    expect(page.get_by_label("old-site", exact=True)).not_to_be_checked()  # archived: never followed
    expect(page.get_by_label("old-site", exact=True)).to_be_disabled()
    expect(page.locator("[data-repository='R_3']")).to_contain_text("Followed once it's active")  # quiet
    expect(page.locator("[data-save]")).to_have_text("Add 2 repositories")
    page.get_by_label("payments-api", exact=True).uncheck()
    # Each control's accessible name, whatever its visible text says.
    expect(page.get_by_role("switch", name="Follow the whole source", exact=True)).to_be_checked()
    expect(page.get_by_role("checkbox", name="web-shop", exact=True)).to_be_checked()
    expect(page.get_by_role("combobox", name="Branch for web-shop", exact=True)).to_be_visible()
    expect(page.get_by_role("searchbox", name="Filter", exact=True)).to_be_visible()
    expect(page.get_by_role("combobox", name="Source", exact=True)).to_be_visible()
    page.evaluate(AXE)
    result = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact))"
        ".map(v => ({id: v.id, help: v.help, nodes: v.nodes.slice(0, 3).map(n => n.target.join(' '))}))"
    )
    assert result == []
    assert errors == []


def test_a_mounted_guide_s_links_and_questions_go_under_its_prefix(page: Page, team_site: TeamSite) -> None:
    save_selection(team_site.paths, "acme", TeamSource(service="github", source="acme-org"))
    write_served_target(team_site.paths, "acme-web-shop", team_site.repository_url)
    team_site.refresh("acme-web-shop")
    team_site.sign_in(page, "/")  # the team page mounts the target it follows
    expect(page.locator("#content a[href='/repos/acme-web-shop/']")).to_be_visible()

    page.goto(f"{team_site.url}/repos/acme-web-shop/")
    ready(page)

    links = page.eval_on_selector_all("a[href^='/']", "links => links.map(link => link.getAttribute('href'))")
    back = page.get_by_role("link", name="← Team acme")
    assert back.get_attribute("href") == "/"  # the one link outside the prefix: the team's page
    links.remove("/")
    assert links and all(link.startswith(("/repos/acme-web-shop/", "/static/")) for link in links), links
    page.route("**/bridge/questions", lambda route: route.abort())  # no assistant runs; only the path matters
    page.locator("[data-ask-open]").first.click()
    page.locator("#ask-question").fill("How often do charges retry?")
    with page.expect_request("**/bridge/questions") as asked:
        page.locator("[data-ask-form] button[type=submit]").click()
    assert urlsplit(asked.value.url).path == "/repos/acme-web-shop/bridge/questions"


def test_a_revisit_counts_only_new_repositories(page: Page, team_site: TeamSite, errors: list[str]) -> None:
    save_selection(team_site.paths, "acme", TeamSource(service="github", source="acme-org"))
    write_served_target(team_site.paths, "acme-web-shop", team_site.repository_url)  # followed already
    gone = RemoteSettings(service="github", source="acme-org", id="R_7", name="legacy", url=team_site.repository_url)
    write_remote_target(team_site.paths, "acme-legacy", gone, "develop", "acme")  # no longer listed
    team_site.sign_in(page)
    choose_the_source(page)
    expect(page.locator("[data-rows] > li")).to_have_count(3)
    expect(page.locator("[data-repository='R_7']")).to_contain_text("No longer in the source")
    expect(page.locator("[data-save]")).to_have_text("Add 1 repository")  # payments-api; web-shop is followed

    page.get_by_label("payments-api", exact=True).uncheck()
    expect(page.locator("[data-save]")).to_have_text("Save")
    page.get_by_label("legacy", exact=True).uncheck()
    page.locator("[data-save]").click()

    expect(page.locator("[data-refresh-state]")).to_have_text(NOTHING_NEW)
    assert load_team(team_site.paths, "acme").sources[0].left_out == ["R_2", "R_7"]
    assert errors == []


def test_a_source_listing_that_fails_says_so(
    page: Page, team_site: TeamSite, monkeypatch: pytest.MonkeyPatch, errors: list[str]
) -> None:
    def failing(organization: str | None = None) -> list[Source]:
        raise HostingError("gh couldn't list the organizations.")

    monkeypatch.setattr(team_site.services["github"], "sources", failing)
    team_site.sign_in(page)

    expect(page.locator("[data-services] li").first).to_contain_text("Its sources couldn't be listed just now.")
    expect(page.locator("[data-source-state]")).to_have_text("Couldn't list them just now.")
    assert errors == []


def test_a_save_while_the_server_stops_says_so(
    page: Page, team_site: TeamSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("codetrail.team.refresh.TeamRefreshJob.start", lambda job, targets: False)
    team_site.sign_in(page)
    choose_the_source(page)

    page.locator("[data-save]").click()

    expect(page.locator("[data-refresh-state]")).to_have_text(
        "Saved, but the server is stopping: the new repositories get ready at the next catch-up."
    )


def test_the_team_page_a_guide_and_back(page: Page, team_site: TeamSite, errors: list[str]) -> None:
    save_selection(team_site.paths, "acme", TeamSource(service="github", source="acme-org"))
    write_served_target(team_site.paths, "acme-web-shop", team_site.repository_url)
    team_site.refresh("acme-web-shop")
    team_site.sign_in(page, "/")
    ready(page)
    page.keyboard.press("g")
    page.keyboard.press("p")  # a guide's shortcut: the team page has no progress page to go to
    expect(page).to_have_url(f"{team_site.url}/")

    page.locator("#sidebar").get_by_role("link", name="web-shop").click()

    expect(page).to_have_url(f"{team_site.url}/repos/acme-web-shop/")
    expect(page.locator(".topbar .target")).to_contain_text("web-shop")
    ready(page)
    page.get_by_role("link", name="← Team acme").click()
    expect(page).to_have_url(f"{team_site.url}/")
    expect(page.locator("#sidebar [aria-current=page]")).to_have_text("Team")
    page.locator("#sidebar").get_by_role("link", name="Add repositories").click()
    expect(page).to_have_url(f"{team_site.url}/team/add")
    assert errors == []


def test_the_azure_organization_field_shows_once_signed_in(page: Page, team_site: TeamSite) -> None:
    signed_in = HostingStatus("azure_devops", True, True, "reader", None)
    team_site.services["azure_devops"] = FakeHosting("azure_devops", [signed_in], [], {}, {})

    team_site.sign_in(page)

    expect(page.locator("[data-services] li").nth(1)).to_contain_text("Signed in as reader")
    expect(page.get_by_label("Azure DevOps organization")).to_be_visible()
