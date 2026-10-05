"""Resizing the sidebar and the Ask panel: by dragging or from the keyboard, within limits, remembered (16.1)."""

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.browser.conftest import Site, ready

pytestmark = pytest.mark.browser
MIN_READING = 480


def width(page: Page, selector: str) -> float:
    box = page.locator(selector).bounding_box()
    assert box is not None
    return float(box["width"])


def drag(page: Page, handle: Locator, to_x: float) -> None:
    box = handle.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(to_x, box["y"] + box["height"] / 2, steps=8)
    page.mouse.up()


def sidebar_maximum(viewport: int) -> int:
    return min(420, int(viewport * 0.30), viewport - MIN_READING)


@pytest.mark.parametrize("viewport", [1280, 1920])
def test_the_sidebar_drags_between_its_limits(page: Page, site: Site, viewport: int) -> None:
    page.set_viewport_size({"width": viewport, "height": 900})
    site.sign_in(page, "/pages/areas/app")
    handle = page.locator('[data-resize="sidebar"]')
    drag(page, handle, viewport)  # far past the maximum
    assert width(page, ".sidebar") == pytest.approx(sidebar_maximum(viewport), abs=1)
    drag(page, handle, 0)  # far past the minimum
    assert width(page, ".sidebar") == pytest.approx(200, abs=1)


def test_the_sidebar_width_is_remembered_and_double_click_resets_it(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    site.sign_in(page, "/pages/areas/app")
    drag(page, page.locator('[data-resize="sidebar"]'), 330)
    remembered = width(page, ".sidebar")
    assert remembered == pytest.approx(330, abs=4)
    page.reload()
    ready(page)
    assert width(page, ".sidebar") == pytest.approx(remembered, abs=1)
    page.locator('[data-resize="sidebar"]').dblclick()
    assert width(page, ".sidebar") == pytest.approx(272, abs=1)
    page.reload()
    ready(page)
    assert width(page, ".sidebar") == pytest.approx(272, abs=1)


def test_the_keyboard_resizes_and_the_handle_reports_its_value(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    site.sign_in(page, "/pages/areas/app")
    handle = page.locator('[data-resize="sidebar"]')
    expect(handle).to_have_attribute("role", "separator")
    handle.focus()
    page.keyboard.press("ArrowRight")
    assert width(page, ".sidebar") == pytest.approx(288, abs=1)
    expect(handle).to_have_attribute("aria-valuenow", "288")
    page.keyboard.press("End")
    assert width(page, ".sidebar") == pytest.approx(sidebar_maximum(1440), abs=1)
    page.keyboard.press("Home")
    assert width(page, ".sidebar") == pytest.approx(200, abs=1)
    page.keyboard.press("m")  # a shortcut key on the handle still works as a shortcut, not a resize
    expect(page.locator("[data-status]")).to_have_attribute("data-status", "read")


def test_the_ask_panel_drags_between_its_limits_and_keeps_the_reading_column(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-ask-open]").first.click()
    drag(page, page.locator('[data-resize="sidebar"]'), 1440)
    handle = page.locator('[data-resize="panel"]')
    drag(page, handle, 0)  # far past the maximum: the panel grows leftward
    panel = width(page, ".ask-panel")
    assert panel <= min(720, 1440 * 0.45) + 1
    assert width(page, "#content") >= MIN_READING - 1
    drag(page, handle, 1440)
    assert width(page, ".ask-panel") == pytest.approx(320, abs=1)


def test_a_narrower_window_shrinks_the_panels_instead_of_the_reading_column(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1920, "height": 900})
    site.sign_in(page, "/pages/areas/app")
    page.locator("[data-ask-open]").first.click()
    drag(page, page.locator('[data-resize="sidebar"]'), 1920)
    drag(page, page.locator('[data-resize="panel"]'), 0)
    page.set_viewport_size({"width": 1240, "height": 900})
    page.wait_for_timeout(200)
    assert width(page, "#content") >= MIN_READING - 1
    assert width(page, ".sidebar") >= 200 - 1


def test_the_handles_are_hidden_on_a_narrow_window(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 800, "height": 900})
    site.sign_in(page, "/pages/areas/app")
    expect(page.locator('[data-resize="sidebar"]')).to_be_hidden()
    page.locator("[data-ask-open]").first.click()
    expect(page.locator('[data-resize="panel"]')).to_be_hidden()
