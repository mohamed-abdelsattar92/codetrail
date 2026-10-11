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


def test_the_repository_charts_follow_the_language(page: Page, site: Site, errors: list[str]) -> None:
    in_arabic(page, site, "/repository")
    timeline = page.locator('[data-chart="timeline"]')
    expect(timeline).to_have_attribute("dir", "ltr")  # charts run left to right, like their numbers
    expect(timeline.locator(".timeline-axis")).to_contain_text("يوليو")  # July, from Babel's Arabic month names
    plot = timeline.locator(".timeline-plot")
    plot.focus()
    page.keyboard.press("End")
    tip = page.locator(".timeline-tip")
    expect(tip).to_be_visible()
    text = tip.text_content() or ""
    assert any(character.isascii() and character.isdigit() for character in text)
    assert not any(0x0660 <= ord(character) <= 0x0669 for character in text)  # no Arabic-Indic digits
    assert errors == []


def test_the_documentation_page_follows_the_language(page: Page, site: Site, errors: list[str]) -> None:
    in_arabic(page, site, "/documentation")
    expect(page.locator("#rationale .doc-about > summary")).to_have_text("ما الذي يُحسب")
    bar = page.locator("#rationale .stack-bar")
    expect(bar).to_have_attribute("dir", "ltr")  # charts run left to right, like their numbers
    legend = page.locator("#rationale .stack-legend").text_content() or ""
    assert any(character.isascii() and character.isdigit() for character in legend)
    assert not any(0x0660 <= ord(character) <= 0x0669 for character in legend)  # no Arabic-Indic digits
    assert errors == []


def test_the_sidebar_sits_on_the_right_in_arabic(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    in_arabic(page, site, "/pages/areas/app")
    box = page.locator("#sidebar").bounding_box()
    assert box is not None and box["x"] > 640
    expect(page.locator("[data-sidebar-toggle]")).to_have_attribute("aria-label", "إخفاء الشريط الجانبي")


def test_the_sidebar_peeks_from_the_right_edge_in_arabic(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 800})
    in_arabic(page, site, "/pages/areas/app")
    page.keyboard.press("\\")
    sidebar = page.locator("#sidebar")
    expect(sidebar).to_be_hidden()
    page.mouse.move(1277, 400)
    expect(sidebar).to_be_visible()
    box = sidebar.bounding_box()
    assert box is not None and box["x"] > 640
