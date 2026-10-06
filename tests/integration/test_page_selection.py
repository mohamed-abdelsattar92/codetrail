"""Which pages an update writes, and in what order (design section 6.2)."""

from pathlib import Path

from codetrail.assistant import AssistantError, PageDraft, PageRequest
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.update import run_update
from tests.fixtures.repos import add_commit
from tests.integration.test_generation import ADR, CHECKS, PLAN, good_page, guide
from tests.integration.test_generation import paths as paths  # the fixture


def set_generation(paths: Paths, settings: str) -> None:
    file = paths.target_file("t")
    text = file.read_text().split("[generation]")[0]
    file.write_text(text + "[generation]\n" + settings)


def test_pages_this_update_changed_come_before_older_backlog(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    target = tmp_path / "target"
    add_commit(target, {"docs/adr/0001-use-fastapi.md": ADR.replace("accepted", "superseded")}, "docs(adr): supersede")
    set_generation(paths, "max_pages_per_update = 0\n")  # nothing written: the ADR's page waits
    assert run_update(paths, "t", claude=FakeAssistant()).generation.left_for_later == ["concepts/fastapi"]  # type: ignore[union-attr]
    add_commit(target, {"services/api/app/routes.py": "from app import db\n"}, "feat(api): routes")
    set_generation(paths, "max_pages_per_update = 1\n")
    generation = run_update(paths, "t", claude=FakeAssistant(page_writer=good_page)).generation
    assert generation is not None
    assert generation.written == ["areas/api"]  # its facts changed in this update
    assert generation.left_for_later == ["concepts/fastapi"]


BAD = PageDraft("Cites [[module:ghost.py]].", CHECKS)


def page_requests(claude: FakeAssistant, page_id: str) -> int:
    return sum(1 for request in claude.requests if isinstance(request, PageRequest) and request.page_id == page_id)


def test_a_page_that_failed_waits_until_its_facts_change(paths: Paths, tmp_path: Path) -> None:
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [BAD, BAD]}, page_writer=good_page)
    assert [page for page, _ in run_update(paths, "t", claude=claude).generation.failed] == ["areas/api"]  # type: ignore[union-attr]
    claude = FakeAssistant(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.skipped == ["areas/api"]
    assert page_requests(claude, "areas/api") == 0  # nothing changed since it failed: no call
    add_commit(tmp_path / "target", {"services/api/app/routes.py": "from app import db\n"}, "feat(api): routes")
    claude = FakeAssistant(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.written == ["areas/api"] and generation.skipped == []
    assert guide(paths).read_page("areas/api") is not None


def test_retry_failed_tries_a_failed_page_again(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], pages={"areas/api": [BAD, BAD]}, page_writer=good_page))
    claude = FakeAssistant(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude, retry_failed=True).generation
    assert generation is not None and generation.written == ["areas/api"]
    generation = run_update(paths, "t", claude=FakeAssistant()).generation  # and a success is forgotten
    assert generation is not None and generation.skipped == [] and generation.written == []


def test_a_provider_error_is_not_remembered_as_a_failure(paths: Paths) -> None:
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [AssistantError("timed out")]}, page_writer=good_page)
    run_update(paths, "t", claude=claude)
    claude = FakeAssistant(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.written == ["areas/api"]
