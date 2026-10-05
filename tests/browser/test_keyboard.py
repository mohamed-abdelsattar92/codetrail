"""The palette and the shortcuts, driven from the keyboard (design sections 16.3 and 16.4)."""

import re
import sys

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser
MODIFIER = "Meta" if sys.platform == "darwin" else "Control"


@pytest.mark.parametrize("keys", [f"{MODIFIER}+k", "/"])
def test_the_palette_finds_a_page_and_opens_it(page: Page, site: Site, errors: list[str], keys: str) -> None:
    site.sign_in(page)
    page.keyboard.press(keys)
    palette = page.locator("[data-palette]")
    expect(palette).to_be_visible()
    page.keyboard.type("ledger")
    expect(page.locator(".palette-option").first).to_contain_text("The ledger")
    page.keyboard.press("Enter")
    expect(page).to_have_url(re.compile(r"/pages/concepts/ledger$"))
    assert errors == []


def test_arrows_move_through_results_and_escape_closes(page: Page, site: Site) -> None:
    site.sign_in(page)
    page.keyboard.press("/")
    page.keyboard.type("retr")
    first = page.locator(".palette-option").first
    expect(first).to_have_attribute("aria-selected", "true")
    page.keyboard.press("ArrowDown")
    expect(first).to_have_attribute("aria-selected", "false")
    page.keyboard.press("Escape")
    expect(page.locator("[data-palette]")).to_be_hidden()


def test_ask_about_opens_the_panel_and_sends_nothing(page: Page, site: Site) -> None:
    site.sign_in(page)
    page.keyboard.press("/")
    page.keyboard.type("how do refunds work")
    option = page.locator(".palette-option", has_text="Ask about")
    expect(option).to_be_visible()
    option.click()
    expect(page.locator("[data-ask]")).to_be_visible()
    expect(page.locator("#ask-question")).to_have_value("how do refunds work")
    assert site.claude.requests == []  # nothing is sent until Send


def test_a_opens_the_ask_panel(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("a")
    expect(page.locator("[data-ask]")).to_be_visible()
    expect(page.locator("#ask-question")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("[data-ask]")).to_be_hidden()


@pytest.mark.parametrize(
    ("key", "path"),
    [("h", "/"), ("p", "/progress"), ("y", "/system"), ("s", "/answers"), ("d", "/digests"), ("r", "/decisions")],
)
def test_g_then_a_letter_goes_places(page: Page, site: Site, key: str, path: str) -> None:
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("g")
    page.keyboard.press(key)
    expect(page).to_have_url(re.compile(re.escape(path) + "$"))


def test_brackets_follow_the_path(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/concepts/retries?path=paths/start")
    page.keyboard.press("]")
    expect(page).to_have_url(re.compile(r"/pages/concepts/ledger\?path=paths/start$"))
    ready(page)
    page.keyboard.press("[")
    expect(page).to_have_url(re.compile(r"/pages/concepts/retries\?path=paths/start$"))
    ready(page)
    page.keyboard.press("[")
    expect(page).to_have_url(re.compile(r"/pages/areas/app\?path=paths/start$"))


def test_m_marks_a_page_read_then_unread(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/areas/app")
    status = page.locator("[data-status]")
    expect(status).to_have_attribute("data-status", "unread")
    page.keyboard.press("m")
    expect(status).to_have_attribute("data-status", "read")
    ready(page)
    page.keyboard.press("m")
    expect(status).to_have_attribute("data-status", "unread")


def test_u_shows_the_estimate_and_cancel_spends_nothing(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("u")
    dialog = page.locator("[data-estimate-dialog]")
    expect(dialog).to_be_visible(timeout=20_000)  # the facts refresh first, and the page polls every 1.5 s
    expect(dialog).to_contain_text("claude-sonnet-5-5")
    page.locator("[data-estimate-cancel]").click()
    expect(dialog).to_be_hidden()
    for _ in range(200):
        if site.decisions:
            break
        page.wait_for_timeout(50)
    assert site.decisions == [False]


def test_question_mark_lists_the_shortcuts(page: Page, site: Site) -> None:
    site.sign_in(page)
    page.keyboard.press("?")
    dialog = page.locator("[data-shortcuts]")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("No shortcut spends anything")


def test_no_shortcut_fires_while_typing(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/areas/app")
    url = page.url
    page.locator(".check textarea").first.click()
    page.keyboard.type("g h a u m ? [ ] /")
    page.keyboard.press("Escape")
    expect(page.locator("[data-ask]")).to_be_hidden()
    expect(page.locator("[data-estimate-dialog]")).to_be_hidden()
    expect(page.locator("[data-shortcuts]")).to_be_hidden()
    expect(page.locator("[data-palette]")).to_be_hidden()
    page.keyboard.press("a")  # Escape left the field focused: still typing
    assert page.url == url
    page.locator("[data-ask-open]").first.click()
    page.keyboard.type("g h u m")
    expect(page.locator("#ask-question")).to_have_value("g h u m")
    assert page.url == url and site.decisions == [] and site.claude.requests == []


def test_the_theme_switch_cycles_and_survives_a_reload(page: Page, site: Site) -> None:
    site.sign_in(page)
    html = page.locator("html")
    toggle = page.locator("[data-theme-toggle]")
    toggle.click()
    expect(html).to_have_attribute("data-theme", "light")
    toggle.click()
    expect(html).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(html).to_have_attribute("data-theme", "dark")
    ready(page)
    toggle.click()
    expect(html).not_to_have_attribute("data-theme", re.compile(".+"))
