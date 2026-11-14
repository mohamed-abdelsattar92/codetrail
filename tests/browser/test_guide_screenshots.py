"""The screenshots in the how-to guides (docs/guides/), taken from the page as the browser tests drive it.

Every screen runs on the browser tests' fake data: the fixture repository, the fake assistant and the fake hosting
service, in temporary folders, so nothing is paid for and no real account is used. Each test skips unless
`CODETRAIL_SCREENSHOTS` names the folder to write to, so `just ci` and `just test-browser` never run them. From the
repository's root:

    CODETRAIL_SCREENSHOTS=docs/images/guides mise exec -- uv run pytest -m browser \
        tests/browser/test_guide_screenshots.py -p no:cacheprovider

A screenshot is named `<guide>-<nn>-<what>.png`, after the guide that shows it.
"""

import os
import shutil
import threading
from pathlib import Path

import pytest
from playwright.sync_api import FloatRect, Locator, Page, ViewportSize, expect

from codetrail.assistant import AnswerChunk, DigestDraft, PageDraft, PageRequest, PlanDraft, Usage, Verdict
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.update import UpdateResult, run_update
from codetrail.web.i18n import installed_languages
from tests.browser.conftest import Site, ready
from tests.browser.test_team_add import (
    FINISHED,
    REFRESH_SECONDS,
    TeamSite,
    choose_the_source,
    team_site,  # noqa: F401 - a fixture the tests below use
)
from tests.browser.test_team_catch_up import (
    CATCH_UP_SECONDS,
    caught_up_once,  # noqa: F401 - a fixture the tests below use
)
from tests.browser.test_team_page import two_guides  # noqa: F401 - a fixture the tests below use
from tests.fixtures.repos import Commit, add_commit

OUTPUT = os.environ.get("CODETRAIL_SCREENSHOTS", "")
pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not OUTPUT, reason="writes the screenshots only when CODETRAIL_SCREENSHOTS names a folder"),
]
DESKTOP: ViewportSize = {"width": 1440, "height": 900}
PHONE: ViewportSize = {"width": 390, "height": 844}
SLIDE_MS = 400  # a drawer or a peek slides in over --duration; screenshots disable animations, not transitions
ADR = (
    "# 0001. Retry failed charges\n\n- Status: accepted\n- Date: 2026-09-01\n\n## Decision\n\n"
    "Retry a failed charge three times, with a growing delay.\n"
)
UPDATE_STEPS: list[dict[str, object]] = [
    {"step": "facts"},
    {"step": "facts_recorded", "changes": 3},
    {"step": "plan"},
    {"step": "planned", "pages": 3, "provider": "claude_code", "model": "claude-sonnet-5-5", "tokens": 9_400},
    {"step": "page", "id": "areas/app", "title": "The app", "attempt": 1, "reason": "new"},
    {"step": "page_written", "id": "areas/app", "title": "The app", "provider": "claude_code",
     "model": "claude-sonnet-5-5", "tokens": 8_200},
    {"step": "page", "id": "concepts/retries", "title": "Payment retries", "attempt": 1, "reason": "new"},
]  # fmt: skip
UPDATE_DONE: list[dict[str, object]] = [
    *UPDATE_STEPS,
    {"step": "page_written", "id": "concepts/retries", "title": "Payment retries", "provider": "claude_code",
     "model": "claude-sonnet-5-5", "tokens": 7_900},
    {"step": "page", "id": "concepts/ledger", "title": "The ledger", "attempt": 1, "reason": "new"},
    {"step": "page_written", "id": "concepts/ledger", "title": "The ledger", "provider": "claude_code",
     "model": "claude-sonnet-5-5", "tokens": 7_600},
    {"step": "digest"},
    {"step": "committed", "pages": 3},
]  # fmt: skip
ANSWER = [  # an answer as a real call reports it: the files it read and what it used
    AnswerChunk("Charges retry **three** times, with a growing delay between attempts (`RETRIES` in app/charges.py)."),
    AnswerChunk(done=True, files_read=["app/charges.py"], cost_usd=0.02,
                usage=Usage("claude_code", "claude-sonnet-5-5", 3_400, 0, 180, 0.02)),
]  # fmt: skip
GRADE_USAGE = Usage("claude_code", "claude-sonnet-5-5", 3_600, 0, 140, 0.01)  # a grade as a real call reports it


def shot(
    page: Page, name: str, full_page: bool = False, part: Locator | None = None, height: int = 0, idle: bool = True
) -> None:
    """Saves the page, or one part of it (its top `height` pixels, if given), once it has settled: no network in
    flight (unless the page polls while the screen lasts, `idle=False`), no animation, no caret."""
    if idle:
        page.wait_for_load_state("networkidle")
    if full_page:
        page.evaluate("window.scrollTo(0, 0)")  # a sticky header is drawn where the window was scrolled to
    path = Path(OUTPUT) / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    box = part.bounding_box() if part is not None and height else None
    if box is not None:
        clip: FloatRect = {"x": box["x"], "y": box["y"], "width": box["width"], "height": min(box["height"], height)}
        page.screenshot(path=str(path), clip=clip, full_page=True, animations="disabled", caret="hide")
    elif part is not None:
        part.screenshot(path=str(path), animations="disabled", caret="hide")
    else:
        page.screenshot(path=str(path), full_page=full_page, animations="disabled", caret="hide")


@pytest.fixture(autouse=True)
def shipped_catalogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `site` fixture's pages speak the languages Codetrail ships, not the browser tests' right-to-left catalog."""
    monkeypatch.setattr("codetrail.web.app.installed_languages", lambda locales=None: installed_languages())


def paths_of(tmp_path: Path) -> Paths:
    """The folders the `site` fixture writes its guide to."""
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")


def rich_writer(request: PageRequest) -> PageDraft:
    """A revision of a page: a documented block (quoting the decision record), an inferred one, a fact link and a
    diagram in place of its two sections, and a new check."""
    how = (
        "A failed charge is retried with a growing delay; the number of attempts lives in [[module:app/charges.py]]."
        '\n\n> [!documented] docs/adr/0001-retry-failed-charges.md#L8\n> "Retry a failed charge three times, with a '
        'growing delay."\n\n> [!inferred]\n> The delay doubles with each attempt, so a charge is given up within a '
        "minute."
    )
    sections = [{"heading": f"## How {request.title} works", "body": how},
                {"heading": "## Where it lives", "body": "{{diagram imports scope=app}}"}]  # fmt: skip
    checks = [{"id": "q1", "question": "Why does a failed charge wait longer before each new attempt?",
               "rubric": [{"point": "p", "grounds": ["app/charges.py"]}]}]  # fmt: skip
    # It reads every file the update changed, as a real revision would: the page isn't left on an old version.
    read = ["app/main.py", "app/charges.py", "docs/adr/0001-retry-failed-charges.md"]
    return PageDraft("", checks, read, sections=sections)


def update_again(tmp_path: Path) -> None:
    """A decision record and a change land, and the guide is updated: every page is revised, with a digest."""
    files: Commit = {"docs/adr/0001-retry-failed-charges.md": ADR, "app/main.py": "from app import charges, ledger\n",
             "app/ledger.py": "BALANCE = 0\n", "app/charges.py": "RETRIES = 3\nBACKOFF_SECONDS = 2\n"}  # fmt: skip
    add_commit(tmp_path / "target", files, "feat(app): wait longer before each retry\n\nWhy: the network asks for it.")
    digest = DigestDraft(
        "Charges now wait longer before each retry",
        "A failed charge now waits two seconds, then longer, before it is tried again.\n\n"
        "> [!inferred]\n> Fewer charges should be refused for being retried too soon.",
        highlights=["Charges wait **two seconds**, then longer, between attempts.",
                    "A decision record explains why: docs/adr/0001."],
    )  # fmt: skip
    # The decision record is a new fact, so the outline is planned again: it keeps its pages.
    claude = FakeAssistant(plans=[PlanDraft([])], page_writer=rich_writer, digests=[digest])
    results: list[UpdateResult] = []
    # Playwright keeps an event loop running on this thread; the update runs its own, so it gets a thread.
    updating = threading.Thread(target=lambda: results.append(run_update(paths_of(tmp_path), "shop", claude=claude)))
    updating.start()
    updating.join()
    generation = results[0].generation
    assert generation is not None and not generation.failed and len(generation.written) == 3, generation


def pass_check(page: Page, answer: str = "So a card network that is briefly down gets time to recover.") -> None:
    page.locator(".check textarea").first.fill(answer)
    page.locator(".check button[type=submit]").first.click()
    expect(page.locator("[data-feedback]").first).to_contain_text("Passed")


def pin(page: Page, page_link: str) -> None:
    row = page.locator("#sidebar li", has=page.locator(f"a[href='{page_link}']")).first
    row.hover()
    row.locator("[data-pin]").click()
    expect(page.locator(f"[data-pins] a.pin[href='{page_link}']")).to_be_visible()


def go(page: Page, site: Site, path: str) -> None:
    page.goto(f"{site.url}{path}")
    ready(page)


def test_first_guide(page: Page, site: Site, tmp_path: Path) -> None:
    page.set_viewport_size(DESKTOP)
    guide = paths_of(tmp_path).target_data("shop") / "guide"
    aside = tmp_path / "guide-aside"
    shutil.move(guide, aside)  # as before the first update: the facts are read, the guide isn't written yet
    site.sign_in(page)
    shot(page, "first-guide-01-home-before-the-first-update")

    site.steps, site.hold = UPDATE_STEPS, True
    page.locator("[data-update-button]").first.click()
    dialog = page.locator("[data-estimate-dialog]")
    expect(dialog).to_be_visible(timeout=20_000)
    shot(page, "first-guide-02-the-estimate")

    page.locator("[data-estimate-go]").click()
    panel = page.locator("[data-update-panel]")
    expect(panel.locator("[data-update-log] li")).to_have_count(len(UPDATE_STEPS), timeout=10_000)
    shot(page, "first-guide-03-the-update-running")

    shutil.move(aside, guide)  # the update has written the guide
    with page.expect_navigation(timeout=10_000):  # done: the page reloads with the new guide
        site.release.set()
    ready(page)
    shot(page, "first-guide-04-home-with-the-guide", full_page=True)

    go(page, site, "/pages/areas/app?path=paths/start")
    shot(page, "first-guide-05-reading-a-page")


@pytest.mark.settings({"learn": {"grading_cooldown_seconds": 0}})  # three answers in a row
def test_reading(page: Page, site: Site, tmp_path: Path) -> None:
    page.set_viewport_size(DESKTOP)
    site.claude.verdicts = [Verdict("pass", [], "Well explained.", 0.01, GRADE_USAGE) for _ in range(3)]
    site.sign_in(page, "/pages/concepts/ledger")
    pass_check(page, "Charges fail sometimes.")  # learned now; the update below changes its check, so it goes stale
    update_again(tmp_path)
    go(page, site, "/pages/areas/app")
    pass_check(page)
    pin(page, "/pages/concepts/retries")

    go(page, site, "/")
    shot(page, "reading-01-home", full_page=True)
    shot(page, "reading-02-the-sidebar", part=page.locator("#sidebar"))

    go(page, site, "/pages/paths/start")
    shot(page, "reading-03-a-path", full_page=True)

    go(page, site, "/pages/areas/app?path=paths/start")
    shot(page, "reading-04-an-area-page", full_page=True)

    go(page, site, "/pages/concepts/retries?path=paths/start")
    expect(page.locator(".diagram-svg img").first).to_be_visible(timeout=20_000)  # D2 compiles its wasm first
    shot(page, "reading-05-a-concept-page", full_page=True)
    header = page.locator(".guide-page").first  # its title and status, cut to the first lines below
    shot(page, "reading-06-before-mark-read", part=header, height=100)
    page.locator("[data-mark-read]").first.click()
    expect(page.locator("[data-status]").first).to_have_attribute("data-status", "read")
    shot(page, "reading-07-after-mark-read", part=header, height=100)

    checks = page.locator(".checks")
    checks.scroll_into_view_if_needed()
    page.locator(".check textarea").first.fill("So a card network that is briefly down gets time to recover.")
    shot(page, "reading-08-a-check-answered", part=checks)
    page.locator(".check button[type=submit]").first.click()
    expect(page.locator("[data-feedback]").first).to_contain_text("Passed")
    shot(page, "reading-09-a-check-graded", part=checks)

    go(page, site, "/progress")
    shot(page, "reading-10-progress", full_page=True)

    go(page, site, "/pages/concepts/ledger")
    shot(page, "reading-11-a-stale-page", full_page=True)


def test_getting_around(page: Page, site: Site) -> None:
    page.set_viewport_size(DESKTOP)
    site.sign_in(page, "/pages/areas/app")
    pin(page, "/pages/concepts/retries")
    pin(page, "/pages/concepts/ledger")
    page.mouse.move(900, 400)
    shot(page, "getting-around-01-pinned-pages")

    page.keyboard.press("\\")
    expect(page.locator("#sidebar")).to_be_hidden()
    shot(page, "getting-around-02-sidebar-hidden")
    page.mouse.move(3, 400)
    expect(page.locator("#sidebar")).to_be_visible()
    page.wait_for_timeout(SLIDE_MS)
    shot(page, "getting-around-03-sidebar-peeking")
    page.keyboard.press("Escape")
    page.mouse.move(900, 400)
    page.keyboard.press("\\")
    expect(page.locator("#sidebar")).to_be_visible()

    page.keyboard.press("?")
    expect(page.locator("[data-shortcuts]")).to_be_visible()
    shot(page, "getting-around-04-keyboard-shortcuts")
    page.keyboard.press("Escape")

    toggle = page.locator("[data-theme-toggle]")
    page.evaluate("document.activeElement.blur()")
    shot(page, "getting-around-05-the-header", part=page.locator(".topbar"))
    toggle.click()
    toggle.click()
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    go(page, site, "/")
    shot(page, "getting-around-06-home-in-dark", full_page=True)
    toggle.click()  # back to following the system's theme
    expect(page.locator("html")).not_to_have_attribute("data-theme", "dark")

    page.locator("[data-language-form] select").select_option("ar")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    go(page, site, "/pages/areas/app")
    shot(page, "getting-around-07-a-page-in-arabic")
    page.locator("[data-language-form] select").select_option("en")
    expect(page.locator("html")).to_have_attribute("dir", "ltr")

    page.set_viewport_size(PHONE)
    go(page, site, "/pages/areas/app")
    shot(page, "getting-around-08-phone-page")
    page.locator("[data-menu]").click()
    expect(page.locator("#sidebar")).to_be_in_viewport()
    page.wait_for_timeout(SLIDE_MS)
    shot(page, "getting-around-09-phone-menu")


def test_search_and_ask(page: Page, site: Site) -> None:
    page.set_viewport_size(DESKTOP)
    site.claude.answers.insert(0, ANSWER)
    site.sign_in(page, "/pages/areas/app")
    page.keyboard.press("/")
    page.keyboard.type("retr")
    expect(page.locator(".palette-option").first).to_contain_text("Payment retries")
    shot(page, "search-ask-01-the-palette")
    page.keyboard.press("Escape")

    page.locator("[data-ask-open]").first.click()
    expect(page.locator("[data-ask]")).to_be_visible()
    page.wait_for_timeout(SLIDE_MS)
    page.locator("#ask-question").fill("How often do charges retry?")
    shot(page, "search-ask-02-a-question-and-its-estimate")
    page.locator("[data-ask-form] button[type=submit]").click()
    expect(page.locator(".ask-answer").first.locator("strong")).to_have_text("three")
    shot(page, "search-ask-03-the-answer")
    page.locator("[data-save-answer]").click()
    expect(page.locator(".ask-actions a")).to_be_visible()
    shot(page, "search-ask-04-the-answer-saved")

    go(page, site, "/answers")
    page.locator("[data-ask-close]").click()
    shot(page, "search-ask-05-saved-answers", full_page=True)

    go(page, site, "/search?q=retr")  # as typed in the palette: the last word matches as a prefix
    shot(page, "search-ask-06-the-search-page", full_page=True)

    page.set_viewport_size(PHONE)
    go(page, site, "/pages/areas/app")
    page.locator("[data-ask-open]").first.click()
    expect(page.locator("[data-ask]")).to_be_visible()
    page.wait_for_timeout(SLIDE_MS)
    shot(page, "search-ask-07-phone-ask-panel")


def test_the_code_pages(page: Page, site: Site, tmp_path: Path) -> None:
    update_again(tmp_path)
    page.set_viewport_size(DESKTOP)
    site.sign_in(page, "/system")
    shot(page, "system-01-the-system-page", full_page=True)
    go(page, site, "/repository")
    shot(page, "repository-01-the-top")
    shot(page, "repository-02-the-whole-page", full_page=True)
    go(page, site, "/documentation")
    shot(page, "documentation-01-the-documentation-page", full_page=True)
    go(page, site, "/decisions")
    shot(page, "decisions-01-the-decisions-page", full_page=True)
    go(page, site, "/areas/app")
    shot(page, "code-01-a-folder", full_page=True)
    go(page, site, "/facts/module:app/main.py")
    shot(page, "code-02-a-fact", full_page=True)
    go(page, site, "/source/app/charges.py")
    shot(page, "code-03-the-source", full_page=True)


def test_digests(page: Page, site: Site, tmp_path: Path) -> None:
    update_again(tmp_path)
    add_commit(tmp_path / "target", {"app/refunds.py": "LIMIT = 30\n"}, "feat(app): refunds")
    page.set_viewport_size(DESKTOP)
    site.sign_in(page)
    shot(page, "digests-01-home-cards", part=page.locator(".home-rail"), height=270)
    go(page, site, "/digests")
    shot(page, "digests-02-the-digests", full_page=True)
    page.locator("#content a[href*='/pages/digests/']").first.click()
    ready(page)
    shot(page, "digests-03-a-digest", full_page=True)


def test_updating(page: Page, site: Site, tmp_path: Path) -> None:
    add_commit(tmp_path / "target", {"app/charges.py": "RETRIES = 5\n"}, "fix(app): retry once more")
    page.set_viewport_size(DESKTOP)
    site.sign_in(page)
    card = page.locator(".rail-card", has=page.locator("#update-title"))
    shot(page, "updating-01-the-behind-card", part=card)

    site.steps, site.hold = UPDATE_STEPS, True
    page.locator("[data-update-button]").first.click()
    expect(page.locator("[data-estimate-dialog]")).to_be_visible(timeout=20_000)
    shot(page, "updating-02-the-estimate")
    page.locator("[data-estimate-go]").click()
    panel = page.locator("[data-update-panel]")
    expect(panel.locator("[data-update-log] li")).to_have_count(len(UPDATE_STEPS), timeout=10_000)
    shot(page, "updating-03-the-update-running")

    with page.expect_navigation(timeout=10_000):  # done: the page reloads with the new guide
        site.release.set()
    ready(page)

    # The done state, over the home page, with every step an update logs: a second update, held at its end.
    site.steps, site.release = UPDATE_DONE, threading.Event()
    page.locator("[data-update-button]").first.click()
    expect(page.locator("[data-estimate-dialog]")).to_be_visible(timeout=20_000)
    page.locator("[data-estimate-go]").click()
    expect(panel.locator("[data-update-log] li")).to_have_count(len(UPDATE_DONE), timeout=10_000)
    with page.expect_navigation(timeout=10_000):  # it shows for a moment before the page reloads
        site.release.set()
        expect(panel.locator("[data-update-state]")).to_have_text("Updated.", timeout=10_000)
        shot(page, "updating-04-the-update-done", idle=False)
    ready(page)


def test_team_add(page: Page, team_site: TeamSite) -> None:  # noqa: F811 - the fixture imported above
    page.set_viewport_size(DESKTOP)
    team_site.sign_in(page)
    expect(page.locator("[data-services] li").first).to_contain_text("Signed in as reader")
    shot(page, "teams-01-add-the-services", full_page=True)
    choose_the_source(page)
    shot(page, "teams-02-add-the-repositories", full_page=True)
    page.get_by_label("payments-api", exact=True).uncheck()
    expect(page.locator("[data-save]")).to_have_text("Add 1 repository")
    shot(page, "teams-03-add-one-left-out", full_page=True)
    page.get_by_label("payments-api", exact=True).check()  # both, as the team page's shots that follow show them
    expect(page.locator("[data-save]")).to_have_text("Add 2 repositories")
    page.locator("[data-save]").click()
    expect(page.locator("[data-refresh-state]")).to_have_text(FINISHED, timeout=REFRESH_SECONDS)
    shot(page, "teams-04-add-saved", full_page=True)


def test_team_page(page: Page, two_guides: TeamSite) -> None:  # noqa: F811 - the fixture imported above
    page.set_viewport_size(DESKTOP)
    two_guides.sign_in(page, "/")
    ready(page)
    shot(page, "teams-05-the-team-page", full_page=True)
    payments = page.locator("[data-team-card='acme-payments-api']")
    shot(page, "teams-06-a-card", part=payments)
    payments.get_by_role("button", name="Mark read").click()
    expect(payments).to_have_count(0)
    shot(page, "teams-07-the-card-read", full_page=True)

    page.locator("#content a[href='/repos/acme-web-shop/']").first.click()
    ready(page)
    shot(page, "teams-13-a-repository-s-guide")
    page.get_by_role("link", name="← Team acme").click()
    ready(page)
    shot(page, "teams-14-back-on-the-team-page")


def test_team_catch_up(page: Page, caught_up_once: TeamSite) -> None:  # noqa: F811 - the fixture imported above
    page.set_viewport_size(DESKTOP)
    caught_up_once.sign_in(page, "/")
    ready(page)
    page.locator("[data-catch-up-button]").click()
    dialog = page.locator("[data-catch-up-dialog]")
    expect(dialog).to_be_visible(timeout=CATCH_UP_SECONDS)
    shot(page, "teams-08-catch-up-the-estimate", idle=False)  # the page polls while the estimate waits
    dialog.get_by_label("payments-api", exact=True).uncheck()
    expect(dialog.locator("[data-overview]")).to_be_hidden()
    shot(page, "teams-09-catch-up-one-unticked", idle=False)

    log = page.locator("[data-catch-up-log]")
    state = page.locator("[data-catch-up-state]")
    with page.expect_navigation(timeout=CATCH_UP_SECONDS):  # done: the page reloads with the new guides
        dialog.locator("[data-catch-up-go]").click()
        expect(log.locator("li", has_text="Writing the digest")).to_be_visible(timeout=CATCH_UP_SECONDS)
        shot(page, "teams-10-catch-up-running", idle=False)
        expect(state).to_have_text("Caught up.", timeout=CATCH_UP_SECONDS)
        shot(page, "teams-11-catch-up-done", idle=False)  # it shows for a moment before the page reloads
    ready(page)
    shot(page, "teams-12-after-the-catch-up", full_page=True)


def test_team_on_a_phone(page: Page, two_guides: TeamSite) -> None:  # noqa: F811 - the fixture imported above
    page.set_viewport_size(PHONE)
    two_guides.sign_in(page, "/")
    ready(page)
    shot(page, "teams-15-phone-team-page", full_page=True)
    page.locator("#content a[href='/repos/acme-web-shop/']").first.click()
    ready(page)
    shot(page, "teams-16-phone-repository")
    page.locator("[data-menu]").click()
    expect(page.locator("#sidebar")).to_be_in_viewport()
    page.wait_for_timeout(SLIDE_MS)
    shot(page, "teams-17-phone-repository-menu")
