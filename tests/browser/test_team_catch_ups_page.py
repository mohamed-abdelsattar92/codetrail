"""The past catch-ups page in a real browser (design section 25.7): reached from the team page, a day from the
calendar, a stretch from the form, the folding sections by keyboard, in light and dark, and at a phone's width.

The team is test_team_page's: web-shop and payments-api, each with a guide and a digest. Its one catch-up finished
now; two earlier ones are written into the team's database, on days before today, each with the digests it wrote.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from playwright.sync_api import Locator, Page, expect

from codetrail.database import connect
from codetrail.guide import GuideRepository
from codetrail.team.state import team_database
from tests.browser.conftest import ready
from tests.browser.test_team_add import TeamSite
from tests.browser.test_team_page import axe_violations, two_guides  # noqa: F401 - a fixture the tests below use

pytestmark = pytest.mark.browser
TARGETS = ("acme-web-shop", "acme-payments-api")


def days_ago(days: int) -> str:
    """Noon UTC, `days` before today, as a catch-up's record holds a time."""
    noon = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    return (noon - timedelta(days=days)).isoformat(timespec="seconds")


def write_past_catch_up(site: TeamSite, days: int, targets: list[str]) -> None:
    """A catch-up that finished `days` ago and caught up `targets`, with the digests their guides now hold."""
    finished_at = days_ago(days)
    started_at = (datetime.fromisoformat(finished_at) - timedelta(minutes=5)).isoformat(timespec="seconds")
    digests = [
        (target, page.id)
        for target in targets
        for page in GuideRepository(site.paths.target_data(target) / "guide").pages("digest")
    ]
    connection = connect(team_database(site.paths, "acme"))
    try:
        connection.execute(
            "INSERT INTO catch_ups (started_at, finished_at, targets, failed, unticked, digests)"
            " VALUES (?, ?, ?, '{}', '[]', ?)",
            (started_at, finished_at, json.dumps(targets),
             json.dumps([list(pair) for pair in digests])),
        )  # fmt: skip
    finally:
        connection.close()


@pytest.fixture
def past_week(two_guides: TeamSite) -> Iterator[TeamSite]:  # noqa: F811 - the fixture imported above
    write_past_catch_up(two_guides, 3, list(TARGETS))
    write_past_catch_up(two_guides, 2, [TARGETS[0]])
    yield two_guides


def day_link(page: Page, days: int) -> Locator:
    return page.locator(f".calendar a[aria-label^='{days_ago(days)[:10]}']")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_past_catch_ups_page(page: Page, past_week: TeamSite, errors: list[str], scheme: str) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    page.set_viewport_size({"width": 1440, "height": 900})
    past_week.sign_in(page, "/")
    ready(page)
    page.get_by_role("link", name="Past catch-ups").first.click()
    ready(page)
    expect(page).to_have_url(f"{past_week.url}/team/catch-ups")
    expect(page.locator("details.catch-up")).to_have_count(3)  # the default range is the last 7 days
    assert axe_violations(page) == []

    # A day in the calendar shows that day.
    link = day_link(page, 2)
    expect(link).to_have_attribute("aria-label", f"{days_ago(2)[:10]}: 1 catch-up")
    fill = "rect => getComputedStyle(rect).fill"
    assert link.locator("rect").evaluate(fill) != page.locator(".calendar rect.heat-0").first.evaluate(fill)  # shaded
    link.click()
    ready(page)
    expect(page.locator("details.catch-up")).to_have_count(1)
    expect(page.locator("details.catch-up")).to_contain_text("1 repository caught up")
    assert page.locator("details.catch-up").evaluate("details => details.open")
    expect(day_link(page, 2)).to_be_visible()  # the calendar didn't move

    # The form shows a stretch.
    page.get_by_label("From", exact=True).fill(days_ago(3)[:10])
    page.get_by_label("To", exact=True).fill(days_ago(2)[:10])
    page.get_by_role("button", name="Show").click()
    ready(page)
    expect(page.locator("details.catch-up")).to_have_count(2)
    assert axe_violations(page) == []

    # Tab reaches a section's summary; Enter opens and closes it.
    older = page.locator("details.catch-up").nth(1)
    summary = older.locator("summary")
    assert not older.evaluate("details => details.open")
    for _ in range(60):
        if summary.evaluate("element => element === document.activeElement"):
            break
        page.keyboard.press("Tab")
    assert summary.evaluate("element => element === document.activeElement")
    page.keyboard.press("Enter")
    assert older.evaluate("details => details.open")
    expect(older).to_contain_text("2 repositories caught up")
    page.keyboard.press("Enter")
    assert not older.evaluate("details => details.open")
    assert axe_violations(page) == []
    assert errors == []


def test_past_catch_ups_at_a_phone_s_width(page: Page, past_week: TeamSite, errors: list[str]) -> None:
    page.set_viewport_size({"width": 375, "height": 812})
    past_week.sign_in(page, "/")
    page.goto(f"{past_week.url}/team/catch-ups")
    ready(page)

    expect(page.locator(".calendar")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".calendar").evaluate("box => getComputedStyle(box).overflowX") in {"auto", "scroll"}
    assert axe_violations(page) == []
    assert errors == []
