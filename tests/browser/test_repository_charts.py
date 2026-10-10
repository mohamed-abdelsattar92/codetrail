"""The Repository page's charts, drawn by the page's own script (design section 20)."""

import json
import re
from datetime import date

import pytest
from playwright.sync_api import Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def open_repository(page: Page, site: Site) -> dict:  # type: ignore[type-arg]
    site.sign_in(page, "/repository")
    ready(page)
    return json.loads(page.locator("[data-repository-data]").text_content() or "{}")  # type: ignore[no-any-return]


def months_between(start: str, end: str) -> int:
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return (last.year - first.year) * 12 + last.month - first.month + 1


def test_the_charts_draw(page: Page, site: Site, errors: list[str]) -> None:
    data = open_repository(page, site)
    bars = page.locator('[data-chart="timeline"] .timeline-bar')
    expect(bars).to_have_count(months_between(data["days"]["start"], data["today"]))
    expect(page.locator('[data-chart="calendar"] rect.day').first).to_be_visible()
    expect(page.locator('[data-chart="heatmap"] .heat-tile').first).to_be_visible()
    expect(page.locator("[data-range-picker]")).to_be_visible()
    assert errors == []


def test_a_range_redraws_the_timeline_by_week(page: Page, site: Site) -> None:
    data = open_repository(page, site)
    page.get_by_role("button", name="1 year").click()
    expect(page.locator('[data-chart="timeline"] .timeline-label').first).to_have_text("Commits per week")
    weeks = page.locator('[data-chart="timeline"] .timeline-bar')
    days = len(data["days"]["counts"])
    assert 1 <= weeks.count() <= min(53, days // 7 + 2)  # a year touches 53 weeks
    expect(page.get_by_role("button", name="1 year")).to_have_attribute("aria-pressed", "true")


def test_hovering_or_focusing_a_bar_reads_its_count(page: Page, site: Site) -> None:
    open_repository(page, site)
    plot = page.locator('[data-chart="timeline"] .timeline-plot')
    plot.scroll_into_view_if_needed()
    box = plot.bounding_box()
    assert box
    plot.hover(position={"x": box["width"] - 3, "y": box["height"] / 2})
    tip = page.locator(".timeline-tip")
    expect(tip).to_be_visible()
    expect(tip).to_contain_text(re.compile(r"\d+ commits?"))
    page.mouse.move(0, 0)
    expect(tip).to_be_hidden()
    plot.focus()
    page.keyboard.press("Home")
    expect(tip).to_be_visible()


def test_choosing_a_release_shows_its_notes(page: Page, site: Site) -> None:
    open_repository(page, site)
    page.locator(".release-mark", has_text="v1.0").click()
    panel = page.locator("[data-release-panel]")
    expect(panel).to_be_visible()
    expect(panel.locator("[data-release-name]")).to_have_text("v1.0")
    expect(panel.locator("[data-release-notes]")).to_contain_text("Charges retry three times.")
    expect(panel.locator("[data-release-meta]")).to_contain_text("The first release")


def test_the_heat_map_switches_levels_and_selects_tiles(page: Page, site: Site) -> None:
    open_repository(page, site)
    panel = page.locator("[data-heat-panel]")
    expect(panel.locator("[data-panel-kind]")).to_have_text("Guide area")
    page.get_by_role("button", name="Files", exact=True).click()
    expect(panel.locator("[data-panel-kind]")).to_have_text("File")
    tiles = page.locator('[data-chart="heatmap"] .heat-tile button')
    last = tiles.last
    name = (last.get_attribute("aria-label") or "").split(":", 1)[0]
    last.click()
    expect(last).to_have_attribute("aria-pressed", "true")
    expect(panel.locator("[data-panel-name]")).to_have_text(name)
    expect(panel.locator("[data-panel-link]")).to_have_text("Open the file")


def test_the_grid_reads_its_cells(page: Page, site: Site) -> None:
    open_repository(page, site)
    cell = page.locator('[data-chart="grid"] td').last
    cell.hover()
    expect(page.locator("[data-grid-status]")).to_have_text(cell.get_attribute("data-cell") or "")


def test_choosing_a_release_keeps_the_focus_on_it(page: Page, site: Site) -> None:
    open_repository(page, site)
    mark = page.locator(".release-mark").first
    mark.focus()
    page.keyboard.press("Enter")
    focused = page.evaluate("document.activeElement.classList.contains('release-mark')")
    assert focused


def test_the_window_starts_at_the_first_commit_and_panning_keeps_it_full(page: Page, site: Site) -> None:
    data = open_repository(page, site)
    expect(page.locator("[data-window-text]")).to_contain_text(f"From {data['days']['start']} ")
    page.get_by_role("button", name="90 days").click()
    earlier = page.get_by_role("button", name="Earlier")
    while earlier.is_enabled():
        earlier.click()
    expect(page.locator('[data-chart="timeline"] .timeline-bar')).to_have_count(90)
