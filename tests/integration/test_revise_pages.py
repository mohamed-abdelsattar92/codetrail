"""An affected page with a few changes is revised, not rewritten (design section 6.3)."""

from pathlib import Path

from codetrail.assistant import PageDraft, PageRequest
from codetrail.assistant.estimate import UpdateEstimate
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.update import run_update
from tests.fixtures.repos import Commit, add_commit
from tests.integration.test_generation import CHECKS, PLAN, good_page, guide
from tests.integration.test_generation import paths as paths  # the fixture
from tests.integration.test_page_selection import set_generation

ROUTES: Commit = {"services/api/app/routes.py": "from app import db\n"}


def revising(sections: list[dict[str, object]]) -> FakeAssistant:
    def write(request: PageRequest) -> PageDraft:
        if not request.changes:
            return good_page(request)
        return PageDraft("", [], ["services/api/app/routes.py"], sections=sections)

    return FakeAssistant(page_writer=write)


def page_requests(claude: FakeAssistant) -> list[PageRequest]:
    return [request for request in claude.requests if isinstance(request, PageRequest)]


def test_a_few_changes_revise_the_page_and_keep_the_rest(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    before = guide(paths).read_page("areas/api")
    assert before is not None
    add_commit(tmp_path / "target", ROUTES, "feat(api): routes")
    claude = revising([{"heading": "## Routes", "body": "The API has routes."}])
    events: list[dict[str, object]] = []
    seen: list[UpdateEstimate] = []

    def confirm(estimate: UpdateEstimate) -> bool:
        seen.append(estimate)
        return True

    run_update(paths, "t", claude=claude, progress=events.append, confirm=confirm)
    assert {line.call.kind: (line.expected, line.maximum) for line in seen[0].lines}["revise"] == (1, 2)
    [request] = page_requests(claude)
    assert request.current_body == before.body and request.current_checks == CHECKS
    assert any(line.startswith("added module module:services/api/app/routes.py") for line in request.changes)
    after = guide(paths).read_page("areas/api")
    assert after is not None
    assert after.body.rstrip("\n") == before.body.rstrip("\n") + "\n\n## Routes\n\nThe API has routes."
    assert after.meta["checks"] == CHECKS  # no new checks: the old ones stay
    files = {item["path"] for item in after.meta["files"]}
    assert files == {"docs/adr/0001-use-fastapi.md", "services/api/app/routes.py"}  # read then, and read now
    page_step = next(event for event in events if event["step"] == "page")
    assert page_step["revise"] is True


def test_nothing_to_change_updates_only_the_pages_record(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    before = guide(paths).read_page("areas/api")
    add_commit(tmp_path / "target", ROUTES, "feat(api): routes")
    events: list[dict[str, object]] = []
    run_update(paths, "t", claude=revising([]), progress=events.append)
    after = guide(paths).read_page("areas/api")
    assert before is not None and after is not None and after.body == before.body
    assert after.meta["scope"]["hash"] != before.meta["scope"]["hash"]
    assert any(event["step"] == "page_written" and event.get("unchanged") for event in events)
    claude = FakeAssistant()
    assert run_update(paths, "t", claude=claude).generation.written == []  # type: ignore[union-attr]
    assert page_requests(claude) == []


def test_a_failed_revision_is_retried_with_its_draft_and_problems(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    add_commit(tmp_path / "target", ROUTES, "feat(api): routes")
    bad = PageDraft("", [], [], sections=[{"heading": "## Routes", "body": "See [[module:ghost.py]]."}])
    good = PageDraft("", [], [], sections=[{"heading": "## Routes", "body": "The API has routes."}])
    claude = FakeAssistant(pages={"areas/api": [bad, good]})
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.written == ["areas/api"]
    retry = page_requests(claude)[1]
    assert "ghost.py" in retry.current_body and any("ghost.py" in problem for problem in retry.problems)
    page = guide(paths).read_page("areas/api")
    assert page is not None and "ghost.py" not in page.body and "The API has routes." in page.body


def test_more_changes_than_the_limit_rewrite_the_page(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    set_generation(paths, "revise_max_changes = 1\n")
    add_commit(tmp_path / "target", ROUTES, "feat(api): routes")  # a module and its import: two changes
    claude = revising([])
    run_update(paths, "t", claude=claude)
    [request] = page_requests(claude)
    assert request.changes == [] and request.current_body == ""
