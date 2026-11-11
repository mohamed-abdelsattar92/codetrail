"""The catch-up from the team page in a real browser (design section 24.8): one estimate with a checkbox per
repository, its totals recomputed as rows are unticked, and the ticked repositories' steps in the panel.

The team app runs as `codetrail team serve` builds it, with the fake assistant writing every guide; the targets fetch
from one repository served on 127.0.0.1.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from codetrail.assistant.estimate import tokens_text
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths, RemoteSettings, TeamSource, write_remote_target
from codetrail.guide import GuideRepository
from codetrail.team.settings import save_selection
from codetrail.update import run_update
from tests.browser.conftest import PLAN, ready, writer
from tests.browser.test_accessibility import AXE
from tests.browser.test_team_add import TeamSite, serving_team
from tests.fixtures.remote import publish
from tests.fixtures.repos import add_commit

pytestmark = pytest.mark.browser
CATCH_UP_SECONDS = 60_000  # the free part fetches and reads every repository's facts


def assistant() -> FakeAssistant:
    return FakeAssistant(plans=[PLAN], page_writer=writer)


def first_guide(paths: Paths, target: str) -> None:
    # Playwright keeps an event loop running on this thread, so the update gets its own.
    updating = threading.Thread(target=run_update, args=(paths, target),
                                kwargs={"claude": assistant(), "confirm": lambda estimate: True})  # fmt: skip
    updating.start()
    updating.join()


@pytest.fixture
def caught_up_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TeamSite]:
    """web-shop and payments-api each have a guide, and a commit has landed in both since."""
    with serving_team(tmp_path, monkeypatch, writer_for=lambda target: assistant(),
                      overview_writer=FakeAssistant()) as site:  # fmt: skip
        save_selection(site.paths, "acme", TeamSource(service="github", source="acme-org"))
        for target, identifier, repository in (("acme-web-shop", "R_1", "web-shop"),
                                               ("acme-payments-api", "R_2", "payments-api")):  # fmt: skip
            remote = RemoteSettings(service="github", source="acme-org", id=identifier, name=repository,
                                    url=site.repository_url)  # fmt: skip
            write_remote_target(site.paths, target, remote, "develop", "acme")
            first_guide(site.paths, target)
        add_commit(site.checkout, {"app/charges.py": "RETRIES = 5\n"}, "fix(app): retry once more")
        publish(site.checkout, tmp_path / "served")
        yield site


def test_team_catch_up_with_one_unticked(page: Page, caught_up_once: TeamSite, errors: list[str]) -> None:
    site = caught_up_once
    payments = GuideRepository(site.paths.target_data("acme-payments-api") / "guide")
    before = payments.head()
    site.sign_in(page, "/")
    ready(page)

    page.locator("[data-catch-up-button]").click()

    dialog = page.locator("[data-catch-up-dialog]")
    expect(dialog).to_be_visible(timeout=CATCH_UP_SECONDS)
    rows = dialog.locator("[data-catch-up-line]")
    expect(rows).to_have_count(2)
    overview = dialog.locator("[data-overview]")
    expect(overview).to_be_visible()
    page.evaluate(AXE)
    violations = page.evaluate(
        "async () => (await axe.run(document.querySelector('[data-catch-up-dialog]'), {resultTypes: ['violations']}))"
        ".violations.filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id)"
    )
    assert violations == []
    at_most = dialog.locator("[data-total='maximum-tokens']")
    all_ticked = at_most.inner_text()

    dialog.get_by_label("payments-api", exact=True).uncheck()

    expect(overview).to_be_hidden()
    expect(at_most).not_to_have_text(all_ticked)
    web_shop = dialog.locator("[data-catch-up-line]", has=page.get_by_label("web-shop", exact=True))
    expect(at_most).to_have_text(tokens_text(int(web_shop.get_attribute("data-maximum-tokens") or "0")))
    budget = float(dialog.locator("[data-budget-usd]").get_attribute("data-budget-usd") or "0")
    maximum_usd = web_shop.get_attribute("data-maximum-usd")
    assert maximum_usd  # the default models have prices
    expect(dialog.locator("[data-total='maximum-usd']")).to_have_text(f"${min(float(maximum_usd), budget):.2f}")
    log = page.locator("[data-catch-up-log]")
    with page.expect_navigation(timeout=CATCH_UP_SECONDS):  # done: the page reloads with the new guides
        dialog.locator("[data-catch-up-go]").click()
        expect(log.locator("li", has_text="Writing the digest")).to_contain_text("web-shop: ")
    ready(page)
    expect(page.locator("[data-catch-up-panel]")).to_be_hidden()
    expect(page.locator("[data-catch-up-button]")).to_be_visible()
    assert payments.head() == before  # unticked: its guide wasn't touched
    assert errors == []
