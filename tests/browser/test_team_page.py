"""The team page in a real browser (design section 24.7): the header, the overview and the cards, in light and dark,
at a phone's width, and Mark read on a card and on the overview.

The team app runs as `codetrail team serve` builds it; web-shop and payments-api each have a guide with an unread
digest, and a commit has landed since. payments-api's facts were refreshed after it, so only web-shop isn't caught up.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from codetrail.config import Paths, RemoteSettings, TeamSource, write_remote_target
from codetrail.database import connect
from codetrail.guide import GuideRepository
from codetrail.learn import LearningState
from codetrail.team.settings import save_selection
from codetrail.team.state import Overviews, TeamState, team_database
from tests.browser.conftest import ready
from tests.browser.test_accessibility import AXE
from tests.browser.test_team_add import TeamSite, serving_team
from tests.browser.test_team_catch_up import first_guide
from tests.fixtures.remote import publish
from tests.fixtures.repos import add_commit

pytestmark = pytest.mark.browser
OVERVIEW = "overviews/2026-11-11-101500"


def write_overview(site: TeamSite) -> None:
    meta: dict[str, object] = {"kind": "overview", "cited": [], "digests": [
        {"target": target, "repository": repository, "id": "digests/2026-11-11-a"}
        for target, repository in (("acme-web-shop", "web-shop"), ("acme-payments-api", "payments-api"))]}  # fmt: skip
    body = "Both repositories retry charges now.\n\n> [!inferred]\n> They may share a client soon."
    Overviews(site.paths.team_data("acme") / "overviews").write(OVERVIEW, meta, body)
    connection = connect(team_database(site.paths, "acme"))
    try:
        state = TeamState(connection)
        state.finish(state.start(), ["acme-web-shop", "acme-payments-api"], OVERVIEW, None)
    finally:
        connection.close()


def read_digests(paths: Paths, target: str) -> None:
    connection = connect(paths.target_data(target) / "codetrail.db")
    try:
        for digest in GuideRepository(paths.target_data(target) / "guide").pages("digest"):
            LearningState(connection).mark_digest_read(digest.id)
    finally:
        connection.close()


@pytest.fixture
def two_guides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TeamSite]:
    with serving_team(tmp_path, monkeypatch) as site:
        save_selection(site.paths, "acme", TeamSource(service="github", source="acme-org"))
        for target, identifier, repository in (("acme-web-shop", "R_1", "web-shop"),
                                               ("acme-payments-api", "R_2", "payments-api")):  # fmt: skip
            remote = RemoteSettings(service="github", source="acme-org", id=identifier, name=repository,
                                    url=site.repository_url)  # fmt: skip
            write_remote_target(site.paths, target, remote, "develop", "acme")
            first_guide(site.paths, target)
        add_commit(site.checkout, {"app/charges.py": "RETRIES = 5\n"}, "fix(app): retry once more")
        publish(site.checkout, tmp_path / "served")
        read_digests(site.paths, "acme-payments-api")  # the reader read its first guide's digest
        first_guide(site.paths, "acme-payments-api")  # caught up: a paid update took the commit in, with a digest
        write_overview(site)
        yield site


def axe_violations(page: Page) -> list[str]:
    page.evaluate(AXE)
    found: list[str] = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact))"
        ".map(v => `${v.id}: ${v.nodes.map(node => node.html).join(' | ')}`)"
    )
    return found


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_team_page_cards(page: Page, two_guides: TeamSite, errors: list[str], scheme: str) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    two_guides.sign_in(page, "/")
    ready(page)
    web_shop = page.locator("[data-team-card='acme-web-shop']")
    payments = page.locator("[data-team-card='acme-payments-api']")
    expect(web_shop).to_contain_text("Not caught up yet: 1 commit touched app.")
    expect(page.locator("[data-overview]")).to_contain_text("Both repositories retry charges now.")
    assert axe_violations(page) == []

    edge = web_shop.evaluate("card => getComputedStyle(card).borderBlockStartStyle")
    assert edge == "dashed"
    assert payments.evaluate("card => getComputedStyle(card).borderBlockStartStyle") == "solid"
    expect(page.locator(".team-card")).to_have_count(2)

    mark_read = payments.get_by_role("button", name="Mark read")
    page.route("**/learn/read", lambda route: route.fulfill(status=500))  # the first try fails: it says so
    mark_read.click()
    expect(payments.locator("[data-read-error]")).to_have_text("Couldn't mark it read. Try again.")
    expect(mark_read).to_be_enabled()
    page.unroute("**/learn/read")

    mark_read.click()

    expect(payments).to_have_count(0)
    page.locator("[data-overview-read]").click()
    expect(page.locator("[data-overview]")).to_have_count(0)
    page.reload()
    ready(page)
    expect(payments).to_have_count(0)
    expect(page.locator("[data-overview]")).to_have_count(0)
    expect(page.locator("[data-nothing-new]")).to_contain_text("payments-api")
    expect(web_shop).to_be_visible()  # not caught up: its card stays, without its digest
    assert errors == ["Failed to load resource: the server responded with a status of 500 (Internal Server Error)"]


def test_team_page_at_a_phone_s_width(page: Page, two_guides: TeamSite, errors: list[str]) -> None:
    page.set_viewport_size({"width": 375, "height": 812})
    two_guides.sign_in(page, "/")
    ready(page)

    expect(page.locator(".team-card")).to_have_count(2)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page) == []
    assert errors == []


def test_the_repositories_heading_lines_up_with_its_links(page: Page, two_guides: TeamSite) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    two_guides.sign_in(page, "/")
    ready(page)
    heading = page.locator("#sidebar .nav-heading .nav-fold-label").bounding_box()
    link = page.locator("#sidebar .nav-section .nav-link .label").first.bounding_box()
    assert heading is not None and link is not None
    assert abs(heading["x"] - link["x"]) <= 1
