"""The sidebar: hiding, peeking, folding and pinning (design section 22)."""

import re

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def wide(page: Page) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})


def hide(page: Page) -> None:
    """Hides the sidebar and waits until it has slid away, so a pointer on the edge doesn't land on it."""
    page.keyboard.press("\\")
    expect(page.locator("#sidebar")).to_be_hidden()


def peek(page: Page, x: int = 3) -> None:
    """Rests the pointer on the edge and waits for the peek, and for its card to finish sliding in."""
    page.mouse.move(x, 400)
    expect(page.locator("body")).to_have_class(re.compile("sidebar-peek"))
    page.wait_for_timeout(300)  # the slide takes --duration (180 ms); a click during it lands on the moving card


def test_the_key_and_the_button_hide_the_sidebar_and_a_reload_keeps_it(
    page: Page, site: Site, errors: list[str]
) -> None:
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
    assert errors == []


def test_the_key_does_nothing_while_typing(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-search-form] input").press("\\")
    expect(page.locator("html")).not_to_have_attribute("data-sidebar", "hidden")


def test_resting_on_the_edge_peeks_and_leaving_hides_it_again(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
    sidebar = page.locator("#sidebar")
    peek(page)
    expect(sidebar).to_be_visible()
    page.mouse.move(900, 400)
    expect(sidebar).to_be_hidden()
    peek(page)
    expect(sidebar).to_be_visible()
    page.keyboard.press("Escape")
    expect(sidebar).to_be_hidden()


def test_the_edge_button_peeks_from_the_keyboard_and_returns_the_focus(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
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
    hide(page)
    peek(page)
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


def test_pinning_adds_a_tile_that_survives_a_reload_and_unpinning_removes_it(
    page: Page, site: Site, errors: list[str]
) -> None:
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
    assert errors == []


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
    hide(page)
    peek(page)
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


def test_a_new_tile_for_the_current_page_is_marked_current(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/concepts/retries")
    row = page.locator("#nav-concepts li", has=page.locator("a[href='/pages/concepts/retries']"))
    row.hover()
    row.locator("[data-pin]").click()
    expect(page.locator("[data-pins] a.pin")).to_have_attribute("aria-current", "page")


def test_tabbing_out_of_the_peek_from_the_edge_closes_it(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
    edge = page.locator("[data-sidebar-edge]")
    edge.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#sidebar")).to_be_visible()
    edge.focus()  # back on the edge: the peek stays open
    expect(page.locator("#sidebar")).to_be_visible()
    page.keyboard.press("Tab")  # on into the page
    expect(page.locator("#sidebar")).to_be_hidden()


def test_a_double_click_on_a_pin_adds_one_tile(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    row = page.locator("#nav-concepts li").first
    row.hover()
    row.locator("[data-pin]").dblclick()
    page.wait_for_timeout(500)
    expect(page.locator("[data-pins] a.pin")).to_have_count(1)
    page.reload()
    ready(page)
    expect(page.locator("[data-pins] a.pin")).to_have_count(1)


def test_the_hidden_state_applies_before_the_page_script_runs(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    page.route("**/static/js/main.js", lambda route: route.abort())  # only theme-init.js runs now
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    expect(page.locator("#sidebar")).to_be_hidden()


def test_the_key_does_nothing_in_the_ask_panel(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("a")
    textarea = page.locator("[data-ask-form] textarea")
    expect(textarea).to_be_focused()
    textarea.press("\\")
    expect(page.locator("html")).not_to_have_attribute("data-sidebar", "hidden")


def test_a_title_with_dollar_patterns_keeps_its_unpin_label(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    row = page.locator("#nav-concepts li").first
    row.locator(".label").evaluate("label => { label.textContent = 'Cost $& more'; }")
    row.hover()
    row.locator("[data-pin]").click()
    expect(page.locator("[data-pins] [data-unpin]")).to_have_attribute("aria-label", "Unpin Cost $& more")


def test_esc_closes_a_hover_peek_without_moving_the_focus(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
    peek(page)
    expect(page.locator("#sidebar")).to_be_visible()
    ask = page.locator(".topbar [data-ask-open]")  # the header's search box would open the palette instead
    ask.focus()
    page.keyboard.press("Escape")
    expect(page.locator("#sidebar")).to_be_hidden()
    expect(ask).to_be_focused()


def test_resting_on_the_edge_keeps_the_peek_open(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
    page.mouse.move(3, 400)
    expect(page.locator("body")).to_have_class(re.compile("sidebar-peek"))
    for step in range(10):  # a hand resting on the edge moves a little while the card slides in under it
        page.mouse.move(3 + step % 2, 400 + step)
        page.wait_for_timeout(60)
    page.wait_for_timeout(500)
    expect(page.locator("body")).to_have_class(re.compile("sidebar-peek"))


def test_clicking_the_edge_after_a_hover_peek_keeps_it_open(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
    peek(page)
    expect(page.locator("#sidebar")).to_be_visible()
    page.mouse.click(3, 400)
    page.wait_for_timeout(400)
    expect(page.locator("#sidebar")).to_be_visible()


def test_focusing_a_row_shows_its_pin(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.locator("#nav-concepts li a").first.focus()
    expect(page.locator("#nav-concepts li [data-pin]").first).to_have_css("opacity", "1")


def test_hiding_from_inside_the_sidebar_moves_the_focus_to_the_toggle(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.locator("#sidebar a[href='/progress']").focus()
    page.keyboard.press("\\")
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    expect(page.locator("[data-sidebar-toggle]")).to_be_focused()


def test_narrowing_the_window_closes_the_peek(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    hide(page)
    peek(page)
    expect(page.locator("#sidebar")).to_be_visible()
    page.set_viewport_size({"width": 375, "height": 812})
    expect(page.locator("body")).not_to_have_class(re.compile("sidebar-peek"))


def test_the_page_prints_without_its_frame(page: Page, site: Site) -> None:
    wide(page)
    site.sign_in(page, "/pages/areas/app")
    page.emulate_media(media="print")
    expect(page.locator(".page-area")).to_have_css("box-shadow", "none")
    expect(page.locator("[data-sidebar-toggle]")).to_be_hidden()
