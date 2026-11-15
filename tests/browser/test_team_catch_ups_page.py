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
from codetrail.guide import Page as GuidePage
from codetrail.team.state import Overviews, team_database
from tests.browser.conftest import ready
from tests.browser.test_team_add import TeamSite
from tests.browser.test_team_page import axe_violations, two_guides  # noqa: F401 - a fixture the tests below use

pytestmark = pytest.mark.browser
TARGETS = ("acme-web-shop", "acme-payments-api")


def days_ago(days: int) -> str:
    """Noon UTC, `days` before today, as a catch-up's record holds a time."""
    noon = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    return (noon - timedelta(days=days)).isoformat(timespec="seconds")


# Each catch-up's own digests: (target, title, highlights, pages changed). A digest is recorded on exactly one catch-up.
CART = (
    "acme-web-shop",
    "The cart keeps its items across sign-in",
    [
        "A cart started signed out is **merged** into the account's cart at sign-in.",
        "> [!inferred]\n> Merging adds quantities, so the same item twice may now show as two.",
    ],
    ["concepts/cart", "areas/app"],
)
RETRIES = (
    "acme-payments-api",
    "Failed charges are retried with a growing delay",
    ["A failed charge is retried **three times**, waiting longer each time."],
    ["concepts/retries"],
)
CANCEL = (
    "acme-web-shop",
    "Orders can be cancelled until they ship",
    ["Cancelling an order before it ships **releases its stock** at once."],
    ["concepts/orders"],
)
SEARCH = (
    "acme-web-shop",
    "Product search ignores accents",
    [
        "Searching for *cafe* now also finds products named *café*.",
        "Results are sorted by **relevance**, then by name.",
    ],
    ["concepts/search", "areas/app"],
)
REFUNDS = (
    "acme-payments-api",
    "Refunds are written to the ledger",
    ["Each refund adds a **ledger entry** that points at the charge it reverses."],
    ["concepts/ledger"],
)


def write_digests(site: TeamSite, days: int, digests: list[tuple[str, str, list[str], list[str]]]) -> str:
    """Writes each digest into its target's guide, dated `days` ago, and returns them as a catch-up's record holds
    them."""
    recorded = []
    for number, (target, title, highlights, changed) in enumerate(digests):
        digest_id = f"digests/{days_ago(days)[:10]}-{number}"
        meta: dict[str, object] = {
            "kind": "digest", "title": title, "from_commit": "a" * 40, "to_commit": "b" * 40,
            "written_at": days_ago(days), "pages_changed": changed, "highlights": highlights,
        }  # fmt: skip
        GuideRepository(site.paths.target_data(target) / "guide").write_page(GuidePage(digest_id, meta, highlights[0]))
        recorded.append([target, digest_id])
    return json.dumps(recorded)


def write_past_catch_up(site: TeamSite, days: int, targets: list[str], digests: str) -> None:
    """A catch-up that finished `days` ago and caught up `targets`, with the digests it wrote."""
    finished_at = days_ago(days)
    started_at = (datetime.fromisoformat(finished_at) - timedelta(minutes=5)).isoformat(timespec="seconds")
    connection = connect(team_database(site.paths, "acme"))
    try:
        connection.execute(
            "INSERT INTO catch_ups (started_at, finished_at, targets, failed, unticked, digests)"
            " VALUES (?, ?, ?, '{}', '[]', ?)",
            (started_at, finished_at, json.dumps(targets), digests),
        )
    finally:
        connection.close()


@pytest.fixture
def past_week(two_guides: TeamSite) -> Iterator[TeamSite]:  # noqa: F811 - the fixture imported above
    """The team's newest catch-up, the fixture's, and two earlier ones, each with digests of its own."""
    overview_id = f"overviews/{days_ago(0)[:10]}-130000"  # the newest catch-up's own, not the shared fixture's
    meta: dict[str, object] = {
        "kind": "overview", "cited": [],
        "digests": [{"target": SEARCH[0], "repository": "web-shop", "id": f"digests/{days_ago(0)[:10]}-0"},
                    {"target": REFUNDS[0], "repository": "payments-api", "id": f"digests/{days_ago(0)[:10]}-1"}],
    }  # fmt: skip
    body = (
        "Search in web-shop and refunds in payments-api both changed.\n\n"
        "> [!inferred]\n> The refund ledger could later feed order history in web-shop."
    )
    Overviews(two_guides.paths.team_data("acme") / "overviews").write(overview_id, meta, body)
    connection = connect(team_database(two_guides.paths, "acme"))
    try:
        connection.execute(
            "UPDATE catch_ups SET digests = ?, overview_id = ?",
            (write_digests(two_guides, 0, [SEARCH, REFUNDS]), overview_id),
        )
    finally:
        connection.close()
    write_past_catch_up(two_guides, 3, list(TARGETS), write_digests(two_guides, 3, [CART, RETRIES]))
    write_past_catch_up(two_guides, 2, [TARGETS[0]], write_digests(two_guides, 2, [CANCEL]))
    yield two_guides


def tab_to(page: Page, target: Locator) -> None:
    """Presses Tab until `target` has the focus."""
    focused = "element => element === document.activeElement"
    for _ in range(400):  # the calendar's 53 weeks have few links; the sidebar and the form come first
        if target.evaluate(focused):
            return
        page.keyboard.press("Tab")
    raise AssertionError("Tab never reached the element")


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
    shown = [fold.text_content() or "" for fold in page.locator("details.catch-up").all()]  # newest first
    for text, own, others in zip(shown, [(SEARCH, REFUNDS), (CANCEL,), (CART, RETRIES)],
                                 [(CART, RETRIES, CANCEL), (SEARCH, REFUNDS, CART, RETRIES), (SEARCH, REFUNDS, CANCEL)],
                                 strict=True):  # fmt: skip
        assert all(digest[1] in text for digest in own)
        assert not any(digest[1] in text for digest in others)
    assert axe_violations(page) == []

    # A day in the calendar shows that day.
    link = day_link(page, 2)
    expect(link).to_have_attribute("aria-label", f"{days_ago(2)[:10]}: 1 catch-up")
    fill = "rect => getComputedStyle(rect).fill"
    assert link.locator("rect").evaluate(fill) != page.locator(".calendar rect.heat-0").first.evaluate(fill)  # shaded
    tab_to(page, link)  # a day with a catch-up is reached by Tab and opened with Enter
    page.keyboard.press("Enter")
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
    tab_to(page, summary)
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
