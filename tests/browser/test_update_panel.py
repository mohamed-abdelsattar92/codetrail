"""The update panel: the update's steps as they happen, closed when it's done, kept open when it fails (15.4)."""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser

HOSTILE = '<img src=x onerror="window.updatePanelInjected = 1">'
STEPS: list[dict[str, object]] = [
    {"step": "page", "id": "areas/app", "title": HOSTILE, "attempt": 1},
    {"step": "page_written", "id": "areas/app", "title": HOSTILE, "provider": "claude_code",
     "model": "claude-sonnet-5-5", "tokens": 8_200, "cost_usd": 0.03},
]  # fmt: skip


def go_ahead(page: Page) -> None:
    page.locator("[data-update-button]").first.click()
    expect(page.locator("[data-estimate-dialog]")).to_be_visible(timeout=20_000)
    page.locator("[data-estimate-go]").click()


def test_the_panel_shows_each_step_then_closes_when_the_update_is_done(
    page: Page, site: Site, errors: list[str]
) -> None:
    site.steps, site.hold = STEPS, True
    site.sign_in(page)
    go_ahead(page)
    panel = page.locator("[data-update-panel]")
    expect(panel).to_be_visible()
    lines = panel.locator("[data-update-log] li")
    expect(lines).to_have_count(2, timeout=10_000)
    expect(lines.nth(1)).to_contain_text(f"Wrote “{HOSTILE}”")  # the title as text, never as markup
    expect(lines.nth(1)).to_contain_text("claude_code · claude-sonnet-5-5")
    assert panel.locator("img").count() == 0 and page.evaluate("window.updatePanelInjected") is None
    with page.expect_navigation(timeout=10_000):  # done: the page reloads with the new guide
        site.release.set()
    ready(page)
    expect(page.locator("[data-update-panel]")).to_be_hidden()
    assert errors == []


def test_a_failed_update_keeps_the_panel_open_with_the_reason(page: Page, site: Site) -> None:
    site.steps, site.failure = STEPS[:1], "the assistant stopped"
    site.sign_in(page)
    go_ahead(page)
    panel = page.locator("[data-update-panel]")
    expect(panel.locator("[data-update-state]")).to_contain_text("the assistant stopped", timeout=10_000)
    page.wait_for_timeout(3_000)  # longer than a finished update stays open
    expect(panel).to_be_visible()
    expect(page.locator("[data-update-button]").first).to_be_enabled()
    panel.locator("[data-update-close]").click()
    expect(panel).to_be_hidden()


def test_the_panel_hides_and_comes_back_while_the_update_runs(page: Page, site: Site) -> None:
    site.steps, site.hold = STEPS[:1], True
    site.sign_in(page)
    go_ahead(page)
    panel = page.locator("[data-update-panel]")
    expect(panel.locator("[data-update-log] li")).to_have_count(1, timeout=10_000)
    page.locator("[data-ask-open]").first.click()  # Ask takes the side
    expect(page.locator("[data-ask]")).to_be_visible()
    expect(panel).to_be_hidden()
    page.locator("[data-update-button]").first.click()  # while it runs, the button shows the update again
    expect(panel).to_be_visible()
    expect(panel.locator("[data-update-log] li")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(page.locator("[data-ask]")).to_be_visible()  # under it, as it was
    assert site.decisions == [True]  # the button didn't start a second update


def test_a_page_opened_during_an_update_shows_it_and_closes_it_when_done(page: Page, site: Site) -> None:
    site.steps, site.hold = STEPS, True
    site.sign_in(page)
    go_ahead(page)
    expect(page.locator("[data-update-log] li")).to_have_count(2, timeout=10_000)
    page.goto(f"{site.url}/pages/areas/app")
    ready(page)
    panel = page.locator("[data-update-panel]")
    expect(panel).to_be_visible()
    expect(panel.locator("[data-update-log] li")).to_have_count(2, timeout=10_000)  # the steps so far, once each
    site.release.set()
    expect(panel.locator("[data-update-state]")).to_have_text("Updated.", timeout=10_000)
    expect(panel).to_be_hidden(timeout=10_000)
