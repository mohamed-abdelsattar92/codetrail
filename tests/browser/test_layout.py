"""The shell's layout across page kinds: the reading column stays put, and tables of one kind line up (16.1)."""

import pytest
from playwright.sync_api import Page

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser


def left_of(page: Page, selector: str) -> float:
    box = page.locator(selector).first.bounding_box()
    assert box is not None
    return box["x"]


def test_the_reading_column_starts_in_one_place_with_or_without_an_outline(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    site.sign_in(page, "/pages/concepts/retries")
    with_outline = left_of(page, ".reading")
    page.goto(f"{site.url}/pages/paths/start")  # a path has no outline
    ready(page)
    assert left_of(page, ".reading") == with_outline


def test_the_system_page_s_tables_share_their_columns(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    site.sign_in(page, "/system")
    tables = page.locator("table.system-parts")
    assert tables.count() >= 2
    columns = tables.evaluate_all(
        "tables => tables.map(table => [...table.querySelectorAll('th')].map(cell => Math.round(cell.offsetLeft)))"
    )
    assert all(found == columns[0] for found in columns)
