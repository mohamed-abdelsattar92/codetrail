"""The catch-up from the team page in a real browser (design section 24.8): one estimate with a checkbox per
repository, its totals recomputed as rows are unticked, and the ticked repositories' steps in the panel.

The team app runs as `codetrail team serve` builds it, with the fake assistant writing every guide; the targets fetch
from one repository served on 127.0.0.1.
"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

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
    maximum_usd, expected_usd = (web_shop.get_attribute(f"data-{kind}-usd") for kind in ("maximum", "expected"))
    assert maximum_usd and expected_usd  # the default models have prices
    capped = min(float(maximum_usd), budget)
    expect(dialog.locator("[data-total='maximum-usd']")).to_have_text(f"${capped:.2f}")
    expect(dialog.locator("[data-total='expected-usd']")).to_have_text(f"${min(float(expected_usd), capped):.2f}")
    expected_tokens = min(int(web_shop.get_attribute("data-expected-tokens") or "0"),
                          int(web_shop.get_attribute("data-maximum-tokens") or "0"))  # fmt: skip
    expect(dialog.locator("[data-total='expected-tokens']")).to_have_text(tokens_text(expected_tokens))
    log = page.locator("[data-catch-up-log]")
    with page.expect_navigation(timeout=CATCH_UP_SECONDS):  # done: the page reloads with the new guides
        dialog.locator("[data-catch-up-go]").click()
        expect(log.locator("li", has_text="Writing the digest")).to_contain_text("web-shop: ")
    ready(page)
    expect(page.locator("[data-catch-up-panel]")).to_be_hidden()
    expect(page.locator("[data-catch-up-button]")).to_be_visible()
    assert payments.head() == before  # unticked: its guide wasn't touched
    assert errors == []


@pytest.fixture
def two_first_guides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TeamSite]:
    """web-shop and payments-api are followed, and neither has a guide yet: each line is a first guide."""
    with serving_team(tmp_path, monkeypatch, writer_for=lambda target: assistant(),
                      overview_writer=FakeAssistant()) as site:  # fmt: skip
        save_selection(site.paths, "acme", TeamSource(service="github", source="acme-org"))
        for target, identifier, repository in (("acme-web-shop", "R_1", "web-shop"),
                                               ("acme-payments-api", "R_2", "payments-api")):  # fmt: skip
            remote = RemoteSettings(service="github", source="acme-org", id=identifier, name=repository,
                                    url=site.repository_url)  # fmt: skip
            write_remote_target(site.paths, target, remote, "develop", "acme")
        yield site


REFUSED = "That estimate isn't waiting any more."


def test_go_ahead_needs_a_ticked_row_and_a_refusal_stays(
    page: Page, two_first_guides: TeamSite, errors: list[str]
) -> None:
    two_first_guides.sign_in(page, "/")
    ready(page)
    page.locator("[data-catch-up-button]").click()
    dialog = page.locator("[data-catch-up-dialog]")
    expect(dialog).to_be_visible(timeout=CATCH_UP_SECONDS)
    expect(dialog.locator("[data-catch-up-line]")).to_contain_text(["first guide", "first guide"])
    go_ahead = dialog.locator("[data-catch-up-go]")

    for repository in ("web-shop", "payments-api"):
        dialog.get_by_label(repository, exact=True).uncheck()

    expect(go_ahead).to_be_disabled()
    dialog.get_by_label("web-shop", exact=True).check()
    expect(go_ahead).to_be_enabled()
    # The server refuses the confirmation (its estimate expired a moment before, say): the reason stays in the panel.
    page.route("**/team/catch-up/confirm", lambda route: route.fulfill(status=400, json={"error": REFUSED}))
    go_ahead.click()
    state = page.locator("[data-catch-up-state]")
    expect(state).to_have_text(REFUSED)
    page.wait_for_timeout(2_000)  # longer than one poll of the status
    expect(state).to_have_text(REFUSED)
    assert all("status of 400" in error for error in errors), errors  # only the browser's note of the refusal


@pytest.mark.parametrize(
    ("tokens", "dollars"), [(950, 0.125), (2_500, 0.375), (3_500, 0.145), (1_150_000, 2.675), (1_250_000, 1.005),
                            (1_750_000, 0.3), (2_450_000, 0.1 + 0.2), (12_345_678, 10.0)],
)  # fmt: skip
def test_the_script_formats_figures_as_the_server_does(
    page: Page, two_first_guides: TeamSite, tokens: int, dollars: float
) -> None:
    two_first_guides.sign_in(page, "/")
    ready(page)

    shown = page.evaluate(
        "async ([tokens, dollars]) => { const script = await import('/static/js/team_catch_up.js');"
        " return [script.tokensText(tokens), script.dollarsText(dollars)]; }",
        [tokens, dollars],
    )

    assert shown == [tokens_text(tokens), f"${dollars:.2f}"]


def test_a_cancelled_catch_up_reloads_the_page(page: Page, two_first_guides: TeamSite, errors: list[str]) -> None:
    two_first_guides.sign_in(page, "/")
    ready(page)
    page.locator("[data-catch-up-button]").click()
    dialog = page.locator("[data-catch-up-dialog]")
    expect(dialog).to_be_visible(timeout=CATCH_UP_SECONDS)

    with page.expect_navigation(timeout=CATCH_UP_SECONDS):  # declined: the cards show the refreshed facts
        dialog.locator("[data-catch-up-cancel]").click()
    ready(page)

    expect(page.locator("[data-catch-up-panel]")).to_be_hidden()
    assert errors == []


def test_a_new_estimate_clears_the_refusal(page: Page, two_first_guides: TeamSite, errors: list[str]) -> None:
    two_first_guides.sign_in(page, "/")
    ready(page)
    renewed: list[bool] = []

    def status(route: Route) -> None:
        found = route.fetch()
        result = found.json()
        if renewed and result.get("estimate_id"):
            result["estimate_id"] = "a-new-estimate"  # as if the refused one expired and a new catch-up asked again
        route.fulfill(response=found, json=result)

    def refuse(route: Route) -> None:
        renewed.append(True)
        route.fulfill(status=400, json={"error": REFUSED})

    page.route("**/team/catch-up/status*", status)
    page.route("**/team/catch-up/confirm", refuse)
    page.locator("[data-catch-up-button]").click()
    dialog = page.locator("[data-catch-up-dialog]")
    expect(dialog).to_be_visible(timeout=CATCH_UP_SECONDS)

    dialog.locator("[data-catch-up-go]").click()

    expect(dialog).to_be_visible()
    expect(page.locator("[data-catch-up-state]")).not_to_have_text(REFUSED)
    page.unroute_all(behavior="ignoreErrors")  # the server stops after the test, under a poll's fetch
    assert all("status of 400" in error for error in errors), errors
