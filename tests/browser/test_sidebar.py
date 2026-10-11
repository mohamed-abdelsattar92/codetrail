"""The sidebar: hiding, peeking, folding and pinning (design section 22)."""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def wide(page: Page) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})


def test_the_key_and_the_button_hide_the_sidebar_and_a_reload_keeps_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    sidebar, toggle = page.locator("#sidebar"), page.locator("[data-sidebar-toggle]")
    page.keyboard.press("\\")
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    expect(sidebar).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")
    page.reload()
    ready(page)
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    toggle.click()
    expect(sidebar).to_be_visible()
    expect(page.locator("html")).not_to_have_attribute("data-sidebar", "hidden")


def test_the_key_does_nothing_while_typing(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-search-form] input").press("\\")
    expect(page.locator("html")).not_to_have_attribute("data-sidebar", "hidden")


def test_resting_on_the_edge_peeks_and_leaving_hides_it_again(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    sidebar = page.locator("#sidebar")
    page.mouse.move(3, 400)
    expect(sidebar).to_be_visible()
    page.mouse.move(900, 400)
    expect(sidebar).to_be_hidden()
    page.mouse.move(3, 400)
    expect(sidebar).to_be_visible()
    page.keyboard.press("Escape")
    expect(sidebar).to_be_hidden()


def test_the_edge_button_peeks_from_the_keyboard_and_returns_the_focus(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    edge = page.locator("[data-sidebar-edge]")
    edge.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#sidebar")).to_be_visible()
    assert page.evaluate("document.querySelector('#sidebar').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(edge).to_be_focused()


def test_following_a_link_from_the_peek_closes_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.mouse.move(3, 400)
    page.locator("#sidebar a[href='/progress']").click()
    page.wait_for_url("**/progress")
    ready(page)
    expect(page.locator("#sidebar")).to_be_hidden()


def test_a_narrow_window_keeps_its_drawer(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.set_viewport_size({"width": 375, "height": 812})
    expect(page.locator("[data-sidebar-toggle]")).to_be_hidden()
    page.locator("[data-menu]").click()
    expect(page.locator("#sidebar")).to_be_in_viewport()
