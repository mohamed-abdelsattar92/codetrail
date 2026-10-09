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


def letter_spacing(page: Page, selector: str) -> str:
    return str(page.locator(selector).first.evaluate("element => getComputedStyle(element).letterSpacing"))


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


def test_right_to_left_text_keeps_its_letters_joined_and_english_keeps_its_tracking(page: Page, site: Site) -> None:
    in_arabic(page, site, "/pages/areas/app")
    assert letter_spacing(page, ".nav-heading") == "normal"
    assert letter_spacing(page, ".checks h2") == "normal"
    assert letter_spacing(page, ".guide-page h1") != "normal"  # the guide's title is English


def test_arabic_text_uses_the_bundled_arabic_typeface(page: Page, site: Site, errors: list[str]) -> None:
    in_arabic(page, site, "/")
    loaded = page.evaluate(
        """async () => {
          await document.fonts.ready;
          return [...document.fonts]
            .filter((face) => face.family.replaceAll('"', "") === "Noto Sans Arabic" && face.status === "loaded")
            .length;
        }"""
    )
    assert loaded == 1
    assert errors == []  # the security policy let the font load


def test_the_ask_panel_slides_in_from_its_own_side(page: Page, site: Site) -> None:
    in_arabic(page, site, "/")
    page.locator("[data-ask-open]").first.click()
    panel = page.locator("[data-ask]")
    expect(panel).to_be_visible()
    assert panel.evaluate("element => getComputedStyle(element).animationName") == "slide-from-start"


def test_the_palette_names_kinds_in_the_reader_s_language_and_keeps_results_english(page: Page, site: Site) -> None:
    in_arabic(page, site, "/")
    page.keyboard.press("Control+k")
    page.locator("[data-palette-input]").fill("retr")
    result = page.locator(".palette-option").filter(has=page.locator(".title", has_text="Payment retries"))
    expect(result.locator(".kind")).to_have_text("مفهوم")  # "Concept"
    assert result.locator(".title").get_attribute("dir") == "ltr"
    assert result.locator(".kind").get_attribute("dir") == "rtl"
    assert result.locator(".snippet").get_attribute("dir") == "ltr"
