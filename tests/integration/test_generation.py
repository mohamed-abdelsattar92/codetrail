"""Generation in an update, with the fake Claude (design section 6)."""

from pathlib import Path

import pytest

from codetrail.claude import ClaudeError, PageDraft, PageRequest, PlanDraft, PlanRequest
from codetrail.claude.fake import FakeClaude
from codetrail.config import Paths, write_target
from codetrail.errors import CodetrailError
from codetrail.guide import GuideRepository
from codetrail.update import run_update
from tests.fixtures.repos import Commit, add_commit, make_repository

ADR = "# 0001. Use FastAPI\n\n- Status: accepted\n\n## Decision\nWe use FastAPI for the API.\n"
FILES: Commit = {
    "services/api/pyproject.toml": '[project]\nname = "api"\ndependencies = ["fastapi==0.1"]\n',
    "services/api/app/__init__.py": "",
    "services/api/app/main.py": "from fastapi import FastAPI\nfrom app import db\n",
    "services/api/app/db.py": "x = 1\n",
    "docs/adr/0001-use-fastapi.md": ADR,
}
PLAN = PlanDraft(
    [
        {"id": "areas/api", "kind": "area", "title": "The API", "scope_paths": ["services/api"], "scope_kinds": [],
         "facts": ["project:services/api", "module:services/api/app/main.py"]},
        {"id": "concepts/fastapi", "kind": "concept", "title": "Why FastAPI", "scope_paths": ["docs/adr"],
         "scope_kinds": ["decision"], "facts": ["decision:ADR-0001"]},
        {"id": "concepts/bad id", "kind": "concept", "title": "Dropped", "scope_paths": ["docs"], "facts": []},
    ]
)  # fmt: skip
CHECKS = [{"id": "q1", "question": "Why FastAPI?", "rubric": [{"point": "The ADR", "grounds": ["decision:ADR-0001"]}]}]


def good_page(request: PageRequest) -> PageDraft:
    body = (
        f"# {request.title}\n\nIt uses [[decision:ADR-0001]].\n\n"
        '> [!documented] docs/adr/0001-use-fastapi.md#L6-L6\n> "We use FastAPI for the API."\n\n'
        "{{diagram imports scope=services/api}}\n"
    )
    return PageDraft(body, CHECKS, ["docs/adr/0001-use-fastapi.md"], 0.01)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    return paths


def guide(paths: Paths) -> GuideRepository:
    return GuideRepository(paths.target_data("t") / "guide")


def test_the_first_update_plans_writes_and_commits(paths: Paths) -> None:
    claude = FakeClaude(plans=[PLAN], page_writer=good_page)
    result = run_update(paths, "t", claude=claude)
    generation = result.generation
    assert generation is not None
    assert sorted(generation.written) == ["areas/api", "concepts/fastapi"]
    assert any("concepts/bad id" in problem for problem in generation.outline_problems)
    page = guide(paths).read_page("areas/api")
    assert page is not None
    assert page.meta["files"] == [
        {"path": "docs/adr/0001-use-fastapi.md", "blob": result.manifest.files["docs/adr/0001-use-fastapi.md"]}
    ]
    assert page.meta["checks"] == CHECKS
    assert generation.digest is not None and guide(paths).read_page(generation.digest) is not None
    assert not guide(paths).has_uncommitted_changes()


def test_an_update_without_changes_writes_nothing(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page))
    claude = FakeClaude(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.written == [] and generation.digest is None
    assert claude.requests == []


def test_a_changed_fact_rewrites_only_its_pages(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page))
    add_commit(
        tmp_path / "target",
        {"services/api/app/routes.py": "from app import db\n"},
        "feat(api): routes\n\nWhy: orders need them",
    )
    claude = FakeClaude(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert generation.written == ["areas/api"]
    assert generation.digest is not None


def test_an_invalid_draft_is_retried_once_then_the_old_page_stays(paths: Paths) -> None:
    bad = PageDraft("Cites [[module:ghost.py]].", CHECKS)
    claude = FakeClaude(plans=[PLAN], pages={"areas/api": [bad, bad]}, page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert [page for page, _ in generation.failed] == ["areas/api"]
    retry = [r for r in claude.requests if isinstance(r, PageRequest) and r.page_id == "areas/api"][1]
    assert any("ghost.py" in problem for problem in retry.problems)
    assert guide(paths).read_page("areas/api") is None
    assert guide(paths).read_page("concepts/fastapi") is not None


def test_a_claude_error_on_one_page_doesnt_stop_the_others(paths: Paths) -> None:
    claude = FakeClaude(plans=[PLAN], pages={"areas/api": [ClaudeError("budget")]}, page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert generation.written == ["concepts/fastapi"]


def test_the_page_budget_leaves_the_rest_for_next_time(paths: Paths) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_pages_per_update = 1\n")
    generation = run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page)).generation
    assert generation is not None and len(generation.written) == 1 and len(generation.left_for_later) == 1
    generation = run_update(paths, "t", claude=FakeClaude(page_writer=good_page)).generation
    assert generation is not None and len(generation.written) == 1


def test_an_unexpected_failure_leaves_the_guide_at_its_last_commit(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page))
    head = guide(paths).head()

    def explode(request: PageRequest) -> PageDraft:
        raise RuntimeError("bug")

    (paths.target_data("t") / "guide" / "concepts" / "fastapi.md").unlink()  # forces a rewrite after commit
    guide(paths).commit("Remove a page")
    head = guide(paths).head()
    with pytest.raises(RuntimeError):
        run_update(paths, "t", claude=FakeClaude(page_writer=explode))
    assert guide(paths).head() == head
    assert not guide(paths).has_uncommitted_changes()


def test_uncommitted_edits_in_the_guide_stop_the_update(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page))
    (paths.target_data("t") / "guide" / "areas" / "api.md").write_text("my edit\n")
    with pytest.raises(CodetrailError, match="uncommitted"):
        run_update(paths, "t", claude=FakeClaude(page_writer=good_page))


def test_facts_only_skips_the_guide(paths: Paths) -> None:
    result = run_update(paths, "t", claude=FakeClaude(), facts_only=True)
    assert result.generation is None


def test_the_first_guide_after_facts_only_updates_gets_a_created_digest(paths: Paths) -> None:
    run_update(paths, "t", facts_only=True)
    generation = run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page)).generation
    assert generation is not None and generation.digest is not None
    digest = guide(paths).read_page(generation.digest)
    assert digest is not None and digest.title == "The guide was created"


def test_the_update_stops_calling_claude_at_its_total_budget(paths: Paths) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_budget_usd_per_update = 0.01\nconcurrency = 1\n")
    claude = FakeClaude(plans=[PLAN], page_writer=good_page)  # each page costs 0.01
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert len(generation.written) == 2 - len(generation.left_for_later)
    assert generation.left_for_later  # the second page waits for the next update


PLAN_WITH_PATHS = PlanDraft(
    PLAN.pages,
    paths=[
        {"id": "paths/start-here", "title": "Start here", "goal": "Understand the API.",
         "steps": ["areas/api", "concepts/fastapi", "concepts/missing"]},
        {"id": "paths/empty", "title": "Nothing", "goal": "x", "steps": ["concepts/missing"]},
    ],
)  # fmt: skip


def test_paths_are_planned_validated_and_written(paths: Paths) -> None:
    generation = run_update(paths, "t", claude=FakeClaude(plans=[PLAN_WITH_PATHS], page_writer=good_page)).generation
    assert generation is not None
    path = guide(paths).read_page("paths/start-here")
    assert path is not None and path.kind == "path"
    assert path.meta["steps"] == ["areas/api", "concepts/fastapi"]
    assert guide(paths).read_page("paths/empty") is None
    assert any("concepts/missing" in problem for problem in generation.outline_problems)


def test_an_outline_without_paths_gets_them_planned(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeClaude(plans=[PLAN], page_writer=good_page))
    outline = guide(paths).read_outline()
    assert outline is not None
    guide(paths).write_outline({"pages": outline["pages"]})  # as written before paths existed
    guide(paths).commit("An outline from before paths")
    claude = FakeClaude(plans=[PlanDraft([], paths=PLAN_WITH_PATHS.paths)], page_writer=good_page)
    run_update(paths, "t", claude=claude)
    request = claude.requests[0]
    assert isinstance(request, PlanRequest) and request.paths_only
    assert guide(paths).read_page("paths/start-here") is not None
