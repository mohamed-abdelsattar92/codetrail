"""The Documentation page's folding sections and filter chips (design section 21)."""

import re

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


def test_the_whole_overview_stays_reachable_on_a_laptop_screen(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 720})
    site.sign_in(page, "/documentation")
    for name in ("commits", "decisions", "coverage"):
        fold(page, name).locator(":scope > summary").click()
    page.locator("#coverage").scroll_into_view_if_needed()
    box = page.locator(".doc-overview").evaluate(
        "element => { const box = element.getBoundingClientRect();"
        " return {top: box.top, bottom: box.bottom, height: innerHeight}; }"
    )
    assert box["top"] >= 0 and box["bottom"] <= box["height"]  # its last tile can be scrolled to, not cut off


def test_a_section_s_arrow_sits_on_its_heading_s_text(page: Page, site: Site) -> None:
    site.sign_in(page, "/documentation")
    for name in ("rationale", "commits"):  # one open, one closed
        heading = fold(page, name).locator(":scope > summary h2")
        arrow = heading.locator(".icon")
        arrow_box, text_box = arrow.bounding_box(), heading.bounding_box()
        assert arrow_box is not None and text_box is not None
        arrow_middle = arrow_box["y"] + arrow_box["height"] / 2
        text_middle = text_box["y"] + text_box["height"] / 2
        assert abs(arrow_middle - text_middle) <= 2, name
    turned = re.compile(r"matrix\([-0-9.e]+, 1, -1, [-0-9.e]+, 0, 0\)")  # rotate(90deg), as Chrome reports it
    expect(fold(page, "rationale").locator(":scope > summary h2 .icon")).to_have_css("transform", turned)
    expect(fold(page, "commits").locator(":scope > summary h2 .icon")).to_have_css("transform", "none")
