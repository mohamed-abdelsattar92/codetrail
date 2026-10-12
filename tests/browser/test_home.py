"""The home page's two columns: the rail sits beside the learning column, and drops below it once the page is narrow
(design section 16.1)."""

import re

import pytest
from playwright.sync_api import FloatRect, Page, expect

from tests.browser.conftest import Site

pytestmark = pytest.mark.browser


def boxes(page: Page) -> tuple[FloatRect, FloatRect]:
    main, rail = page.locator(".home-main").bounding_box(), page.locator(".home-rail").bounding_box()
    assert main and rail
    return main, rail


def test_the_rail_sits_beside_the_learning_column(page: Page, site: Site, errors: list[str]) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    site.sign_in(page)
    main, rail = boxes(page)
    assert rail["x"] >= main["x"] + main["width"]
    assert rail["y"] == main["y"]
    assert errors == []


def test_the_rail_drops_below_when_the_ask_panel_takes_the_room(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 900})  # wide enough for both, until the panel opens
    site.sign_in(page)
    main, rail = boxes(page)
    assert rail["x"] >= main["x"] + main["width"]
    page.locator("[data-ask-open]").first.click()
    expect(page.locator("body")).to_have_class(re.compile("ask-open"))
    page.wait_for_timeout(300)  # the panel slides in over --duration
    main, rail = boxes(page)
    assert rail["y"] >= main["y"] + main["height"]
    assert rail["x"] == main["x"]
