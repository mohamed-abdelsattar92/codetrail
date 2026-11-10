"""A guide mounted under /repos/<target>/, as the team page serves it (design section 24.7).

Every guide on the team's server shares one origin, so what a guide's scripts remember is kept under its prefix.
"""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = [pytest.mark.browser, pytest.mark.mounted]


def stored(page: Page) -> dict[str, str]:
    return dict(page.evaluate("Object.fromEntries(Object.entries(window.localStorage))"))


def test_a_mounted_guide_remembers_under_its_prefix(page: Page, site: Site, errors: list[str]) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    site.sign_in(page)
    assert page.url.endswith("/repos/shop/")
    page.keyboard.press("\\")
    page.locator("[data-theme-toggle]").click()

    assert stored(page) == {"/repos/shopcodetrail.sidebar": "hidden", "/repos/shopcodetrail.theme": "light"}
    page.reload()  # applied before the page script runs, from the prefixed keys
    expect(page.locator("html")).to_have_attribute("data-sidebar", "hidden")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    ready(page)
    assert errors == []


def test_the_palette_opens_a_page_under_the_prefix(page: Page, site: Site, errors: list[str]) -> None:
    site.sign_in(page)
    page.keyboard.press("/")
    expect(page.locator("[data-palette]")).to_be_visible()
    page.keyboard.type("ledger")
    expect(page.locator(".palette-option").first).to_contain_text("The ledger")
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{site.url}/pages/concepts/ledger")
    assert errors == []
