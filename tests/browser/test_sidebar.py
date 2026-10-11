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


def test_a_section_folds_and_stays_folded_but_shows_the_current_page(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/concepts/retries")
    page.locator('[data-fold="concepts"]').click()
    expect(page.locator('[data-fold="concepts"]')).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#nav-concepts a[href='/pages/concepts/ledger']")).to_be_hidden()
    expect(page.locator("#nav-concepts a[href='/pages/concepts/retries']")).to_be_visible()
    page.reload()
    ready(page)
    expect(page.locator("html")).to_have_attribute("data-folded", "concepts")
    expect(page.locator('[data-fold="concepts"]')).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#nav-concepts a[href='/pages/concepts/ledger']")).to_be_hidden()


def test_pinning_adds_a_tile_that_survives_a_reload_and_unpinning_removes_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    row = page.locator("#nav-concepts li", has=page.locator("a[href='/pages/concepts/retries']"))
    row.hover()
    row.locator("[data-pin]").click()
    tile = page.locator("[data-pins] a.pin[href='/pages/concepts/retries']")
    expect(tile).to_have_text("PR")
    expect(row.locator("[data-pin]")).to_have_attribute("aria-pressed", "true")
    page.reload()
    ready(page)
    expect(tile).to_have_attribute("aria-label", "Payment retries")
    tile.hover()
    page.locator("[data-pins] [data-unpin='concepts/retries']").click()
    expect(tile).to_have_count(0)
    page.reload()
    ready(page)
    expect(tile).to_have_count(0)


@pytest.mark.settings({"ui": {"max_pins": 1}})
def test_at_the_limit_the_pin_buttons_say_so(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    row = page.locator("#nav-areas li").first
    row.hover()
    row.locator("[data-pin]").click()
    expect(page.locator("[data-pins] a.pin")).to_have_count(1)
    other = page.locator("#nav-concepts [data-pin]").first
    expect(other).to_be_disabled()
    expect(other).to_have_attribute("title", "Unpin a page first")


def test_pinning_from_the_peek_keeps_it_open(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.mouse.move(3, 400)
    row = page.locator("#nav-concepts li").first
    row.hover()
    row.locator("[data-pin]").click()
    expect(page.locator("[data-pins] a.pin")).to_have_count(1)
    expect(page.locator("#sidebar")).to_be_visible()


def test_a_failed_pin_request_changes_nothing(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.route("**/settings/pins", lambda route: route.fulfill(status=403))
    row = page.locator("#nav-concepts li").first
    row.hover()
    row.locator("[data-pin]").click()
    expect(row.locator("[data-pin]")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("[data-pins] a.pin")).to_have_count(0)
