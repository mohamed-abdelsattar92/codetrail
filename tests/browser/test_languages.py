"""The page in the reader's language: catalog text where the script writes it, and right-to-left (design 7.2)."""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def in_arabic(page: Page, site: Site, path: str) -> None:
    site.sign_in(page)
    page.locator("[data-language-form] select").select_option("ar")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    page.goto(f"{site.url}{path}")
    ready(page)


def test_a_graded_answer_shows_its_verdict_in_words(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/areas/app")
    page.locator(".check textarea").first.fill("Charges fail sometimes.")
    page.locator(".check button[type=submit]").first.click()
    expect(page.locator("[data-feedback]").first).to_contain_text("Passed: Well explained.")


def test_the_verdict_follows_the_language(page: Page, site: Site) -> None:
    in_arabic(page, site, "/pages/areas/app")
    page.locator(".check textarea").first.fill("Charges fail sometimes.")
    page.locator(".check button[type=submit]").first.click()
    expect(page.locator("[data-feedback]").first).to_contain_text("نجحت: Well explained.")
