"""The Documentation page's folding sections and filter chips (design section 21)."""

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.browser.conftest import Site

pytestmark = pytest.mark.browser


def fold(page: Page, name: str) -> Locator:
    return page.locator(f"#{name} > details.doc-fold")


def test_the_section_most_in_need_opens_and_the_others_on_demand(page: Page, site: Site, errors: list[str]) -> None:
    site.sign_in(page, "/documentation")
    expect(fold(page, "rationale")).to_have_attribute("open", "")  # every share is 0%: the first opens
    expect(fold(page, "commits")).not_to_have_attribute("open", "")
    fold(page, "commits").locator(":scope > summary").click()
    expect(fold(page, "commits")).to_have_attribute("open", "")
    summary = fold(page, "decisions").locator(":scope > summary")
    summary.focus()
    page.keyboard.press("Enter")
    expect(fold(page, "decisions")).to_have_attribute("open", "")
    assert errors == []


def test_a_chip_shows_one_kind_and_says_so(page: Page, site: Site) -> None:
    site.sign_in(page, "/documentation")
    fold(page, "coverage").locator(":scope > summary").click()
    chips = page.locator("#coverage [data-filter]")
    expect(chips).to_be_visible()
    chips.locator('[data-show="resource"]').click()
    expect(chips.locator('[data-show="resource"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#coverage li[data-kind="project"]')).to_be_hidden()
    expect(page.locator('#coverage li[data-kind="resource"]')).to_be_visible()
    expect(page.locator("#coverage [data-filter-status]")).to_have_text("Resources · 1")
    chips.locator('[data-show="all"]').click()
    expect(page.locator('#coverage li[data-kind="project"]')).to_be_visible()


@pytest.mark.settings({"metrics": {"max_listed": 1}})
def test_a_chip_filters_the_folded_rest_too(page: Page, site: Site) -> None:
    site.sign_in(page, "/documentation")
    fold(page, "coverage").locator(":scope > summary").click()
    rest = page.locator("#coverage [data-filterable] details.more")  # the unexplained facts fold their own rest
    folded = rest.locator("li[data-kind]").get_attribute("data-kind")
    shown = "project" if folded == "resource" else "resource"
    page.locator(f'#coverage [data-show="{shown}"]').click()
    expect(rest).to_be_hidden()  # nothing of that kind folded away: no "0 more"
    page.locator(f'#coverage [data-show="{folded}"]').click()
    expect(rest).to_be_visible()
    expect(rest.locator(f'[data-more="{folded}"]')).to_have_text("1 more")
    expect(rest.locator('[data-more="all"]')).to_be_hidden()


def test_the_overview_stays_in_view_then_becomes_a_row(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 720})
    site.sign_in(page, "/documentation")
    overview = page.locator(".doc-overview")
    assert overview.evaluate("element => getComputedStyle(element).position") == "sticky"
    page.set_viewport_size({"width": 375, "height": 812})
    assert overview.evaluate("element => getComputedStyle(element).position") == "static"
    columns = overview.evaluate("element => getComputedStyle(element).gridTemplateColumns")
    assert len(columns.split()) >= 2  # the tiles side by side, as a row
