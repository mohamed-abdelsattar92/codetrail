"""The Ask panel keeps the session's answers across pages, and saving one adds it to the guide (design 16.4)."""

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def test_an_answer_stays_in_the_panel_across_pages_and_saves_to_the_guide(
    page: Page, site: Site, errors: list[str]
) -> None:
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-ask-open]").first.click()
    page.locator("#ask-question").fill("How often do charges retry?")
    page.locator("[data-ask-form] button[type=submit]").click()
    answer = page.locator(".ask-answer").first
    expect(answer.locator("strong")).to_have_text("three")
    assert len(site.claude.requests) == 1

    page.goto(f"{site.url}/pages/concepts/ledger")
    ready(page)
    expect(page.locator("[data-ask]")).to_be_visible()  # still open in this tab
    expect(page.locator(".ask-question")).to_have_text(["How often do charges retry?"])

    page.locator("[data-save-answer]").click()
    saved = page.locator(".ask-actions a")
    expect(saved).to_be_visible()
    expect(page.locator("[data-nav-answers] a").first).to_have_text("How often do charges retry?")

    page.keyboard.press("Escape")
    page.keyboard.press("/")
    page.keyboard.type("charges retry three")
    expect(page.locator(".palette-option", has_text="How often do charges retry?")).to_be_visible()
    assert errors == []


def test_the_panel_closed_stays_closed(page: Page, site: Site) -> None:
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-ask-open]").first.click()
    page.locator("[data-ask-close]").click()
    page.goto(f"{site.url}/")
    expect(page.locator("[data-ask]")).to_be_hidden()


def test_an_answer_with_a_diagram_draws_it_in_the_panel(page: Page, site: Site, errors: list[str]) -> None:
    from codetrail.assistant import AnswerChunk

    site.claude.answers.insert(0, [AnswerChunk("The app's modules:\n\n{{diagram imports scope=app}}\n"),
                                   AnswerChunk(done=True)])  # fmt: skip
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-ask-open]").first.click()
    page.locator("#ask-question").fill("Draw the architecture")
    page.locator("[data-ask-form] button[type=submit]").click()
    answer = page.locator(".ask-answer").first
    expect(answer.locator(".diagram-svg svg")).to_be_visible()
    expect(answer.locator("pre.diagram")).to_be_hidden()  # the Mermaid source gives way to the drawing
    page.goto(f"{site.url}/pages/concepts/ledger")  # the session's answers come back drawn too
    ready(page)
    expect(page.locator(".ask-answer").first.locator(".diagram-svg svg")).to_be_visible()
    assert errors == []
