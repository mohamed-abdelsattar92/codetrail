"""Every page type, in light and dark: no console errors or blocked content, and no serious accessibility issue.

axe-core runs through `page.evaluate`, which the page's security policy doesn't govern, so the policy stays exactly
as served (design section 16.7). It is never injected as a script tag.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.browser.conftest import Site

pytestmark = pytest.mark.browser
AXE = (Path(__file__).parent / "vendor" / "axe.min.js").read_text()
PAGES = [
    "/", "/progress", "/answers", "/digests", "/decisions", "/search?q=retry", "/pages/areas/app",
    "/pages/concepts/retries?path=paths/start", "/pages/paths/start", "/facts/module:app/main.py",
    "/source/app/main.py", "/areas/app", "/system", "/areas/infra", "/documentation", "/repository", "/nowhere",
]  # fmt: skip


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("path", PAGES)
def test_pages_have_no_errors_and_no_serious_accessibility_issue(
    page: Page, site: Site, errors: list[str], path: str, scheme: str
) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    site.sign_in(page, path)
    page.wait_for_load_state("networkidle")
    page.evaluate(AXE)
    result = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact))"
        ".map(v => ({id: v.id, help: v.help, nodes: v.nodes.slice(0, 3).map(n => n.target.join(' '))}))"
    )
    assert result == []
    expected = ["status of 404"] if path == "/nowhere" else []  # the page itself is a 404
    assert [error for error in errors if not any(text in error for text in expected)] == []


def test_the_charts_have_text_alternatives(page: Page, site: Site) -> None:
    site.sign_in(page, "/repository")
    page.wait_for_load_state("networkidle")
    plot = page.locator('[data-chart="timeline"] .timeline-plot')
    assert (plot.get_attribute("aria-label") or "").startswith("Commits per month.")
    calendar = page.locator('[data-chart="calendar"] svg')
    assert calendar.get_attribute("role") == "img" and calendar.get_attribute("aria-label")
    tiles = page.locator('[data-chart="heatmap"] .heat-tile button')
    assert all((tiles.nth(index).get_attribute("aria-label") or "").count(":") == 1 for index in range(tiles.count()))


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_documentation_page_with_every_section_open(page: Page, site: Site, errors: list[str], scheme: str) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    site.sign_in(page, "/documentation")
    for fold in page.locator("details.doc-fold").all():
        if fold.get_attribute("open") is None:
            fold.locator(":scope > summary").click()
    page.evaluate(AXE)
    result = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id)"
    )
    assert result == []
    assert errors == []


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("state", ["hidden", "peek"])
def test_the_sidebar_hidden_or_peeking(page: Page, site: Site, errors: list[str], scheme: str, state: str) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    page.set_viewport_size({"width": 1280, "height": 800})
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("\\")
    if state == "peek":
        page.mouse.move(3, 400)
        page.locator("#sidebar").wait_for(state="visible")
    page.evaluate(AXE)
    result = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id)"
    )
    assert result == []
    assert errors == []
