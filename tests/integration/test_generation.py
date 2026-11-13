"""Generation in an update, with the fake Claude (design section 6)."""

from dataclasses import replace
from pathlib import Path

import anyio
import pytest

from codetrail.assistant import (
    AssistantError,
    DigestDraft,
    PageDraft,
    PageRequest,
    PaidCallFailed,
    PlanDraft,
    PlanRequest,
    Usage,
)
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths, write_target
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.generate.run import SharedBudget
from codetrail.guide import GuideRepository, Page
from codetrail.update import run_update
from tests.fixtures.repos import Commit, add_commit, fake_github_token, make_repository

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
BRIEF = "The key file is [[module:services/api/app/main.py]]."
CHECKS = [{"id": "q1", "question": "Why FastAPI?", "rubric": [{"point": "The ADR", "grounds": ["decision:ADR-0001"]}]}]


def good_page(request: PageRequest) -> PageDraft:
    body = (
        f"# {request.title}\n\nIt uses [[decision:ADR-0001]].\n\n"
        '> [!documented] docs/adr/0001-use-fastapi.md#L6-L6\n> "We use FastAPI for the API."\n\n'
        "{{diagram imports scope=services/api}}\n"
    )
    return PageDraft(body, CHECKS, ["docs/adr/0001-use-fastapi.md"], 0.01, brief=BRIEF)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    return paths


def guide(paths: Paths) -> GuideRepository:
    return GuideRepository(paths.target_data("t") / "guide")


def test_the_first_update_plans_writes_and_commits(paths: Paths) -> None:
    claude = FakeAssistant(plans=[PLAN], page_writer=good_page)
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
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    claude = FakeAssistant(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.written == [] and generation.digest is None
    assert claude.requests == []


def test_a_changed_fact_rewrites_only_its_pages(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    add_commit(
        tmp_path / "target",
        {"services/api/app/routes.py": "from app import db\n"},
        "feat(api): routes\n\nWhy: orders need them",
    )
    claude = FakeAssistant(page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert generation.written == ["areas/api"]
    assert generation.digest is not None


def test_an_invalid_draft_is_retried_once_then_the_old_page_stays(paths: Paths) -> None:
    bad = PageDraft("Cites [[module:ghost.py]].", CHECKS, brief=BRIEF)
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [bad, bad]}, page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert [page for page, _ in generation.failed] == ["areas/api"]
    retry = [r for r in claude.requests if isinstance(r, PageRequest) and r.page_id == "areas/api"][1]
    assert any("ghost.py" in problem for problem in retry.problems)
    assert guide(paths).read_page("areas/api") is None
    assert guide(paths).read_page("concepts/fastapi") is not None


def test_a_claude_error_on_one_page_doesnt_stop_the_others(paths: Paths) -> None:
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [AssistantError("budget")]}, page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert generation.written == ["concepts/fastapi"]


def test_the_page_budget_leaves_the_rest_for_next_time(paths: Paths) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_pages_per_update = 1\n")
    generation = run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page)).generation
    assert generation is not None and len(generation.written) == 1 and len(generation.left_for_later) == 1
    generation = run_update(paths, "t", claude=FakeAssistant(page_writer=good_page)).generation
    assert generation is not None and len(generation.written) == 1


def test_an_unexpected_failure_leaves_the_guide_at_its_last_commit(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    head = guide(paths).head()

    def explode(request: PageRequest) -> PageDraft:
        raise RuntimeError("bug")

    (paths.target_data("t") / "guide" / "concepts" / "fastapi.md").unlink()  # forces a rewrite after commit
    guide(paths).commit("Remove a page")
    head = guide(paths).head()
    with pytest.raises(RuntimeError):
        run_update(paths, "t", claude=FakeAssistant(page_writer=explode))
    assert guide(paths).head() == head
    assert not guide(paths).has_uncommitted_changes()


def test_uncommitted_edits_in_the_guide_stop_the_update(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    (paths.target_data("t") / "guide" / "areas" / "api.md").write_text("my edit\n")
    with pytest.raises(CodetrailError, match="uncommitted"):
        run_update(paths, "t", claude=FakeAssistant(page_writer=good_page))


def test_facts_only_skips_the_guide(paths: Paths) -> None:
    result = run_update(paths, "t", claude=FakeAssistant(), facts_only=True)
    assert result.generation is None


def test_the_first_guide_after_facts_only_updates_gets_a_created_digest(paths: Paths) -> None:
    run_update(paths, "t", facts_only=True)
    generation = run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page)).generation
    assert generation is not None and generation.digest is not None
    digest = guide(paths).read_page(generation.digest)
    assert digest is not None and digest.title == "The guide was created"


def test_the_update_stops_calling_claude_at_its_total_budget(paths: Paths) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_budget_usd_per_update = 0.01\nconcurrency = 1\n"
                    "max_budget_usd_per_call = 0.01\n")  # fmt: skip
    claude = FakeAssistant(plans=[PLAN], page_writer=good_page)  # each page costs 0.01
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert len(generation.written) == 2 - len(generation.left_for_later)
    assert generation.left_for_later  # the second page waits for the next update


def test_shared_budget_stops_new_calls(paths: Paths) -> None:
    one_at_a_time(paths, per_call_usd=0.01)
    shared = SharedBudget(0.01)  # each page costs 0.01
    claude = FakeAssistant(plans=[PLAN], page_writer=good_page)
    generation = run_update(paths, "t", claude=claude, shared_budget=shared).generation
    assert generation is not None
    assert len(generation.written) == 1 and len(generation.left_for_later) == 1
    assert shared.exhausted


def test_an_exhausted_shared_budget_stops_a_first_run_before_its_plan(paths: Paths) -> None:
    shared = SharedBudget(1.0)
    assert shared.reserve(1.0)
    shared.settle(1.0, 1.0)
    claude = FakeAssistant(plans=[PLAN], page_writer=good_page)
    with pytest.raises(CodetrailError, match="team's budget is spent"):
        run_update(paths, "t", claude=claude, shared_budget=shared)
    assert claude.requests == []


def test_no_page_retry_once_the_shared_budget_runs_out(paths: Paths) -> None:
    one_at_a_time(paths, per_call_usd=0.01)
    bad = PageDraft("Cites [[module:ghost.py]].", CHECKS, cost_usd=0.01, brief=BRIEF)
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [bad, bad]}, page_writer=good_page)
    generation = run_update(paths, "t", claude=claude, shared_budget=SharedBudget(0.01)).generation
    assert generation is not None
    page_requests = [request for request in claude.requests if isinstance(request, PageRequest)]
    assert [request.page_id for request in page_requests] == ["areas/api"]  # one attempt, and nothing after it
    assert "areas/api" in generation.left_for_later


def test_shared_budget_counts_each_call() -> None:
    shared = SharedBudget(1.0)
    for _ in range(2):
        assert shared.reserve(0.5)
        shared.settle(0.5, 0.25)
    assert shared.spent_usd == 0.5
    assert not shared.exhausted


def test_a_call_starts_only_when_its_cap_fits_beside_the_calls_in_flight() -> None:
    """Each call reserves its cap before it starts, so calls in flight can't take the total past the limit."""
    shared = SharedBudget(1.0)
    assert shared.reserve(0.4) and shared.reserve(0.4)
    assert not shared.reserve(0.4)  # 1.2 if all three spent their cap
    shared.settle(0.4, 0.1)  # one ended, having spent less than its cap
    assert shared.reserve(0.4)
    assert shared.spent_usd == pytest.approx(0.1)
    shared = SharedBudget(0.3)
    assert all(shared.reserve(0.1) for _ in range(3))  # three tenths fit in three tenths, rounding aside


class SlowPages(FakeAssistant):
    async def write_page(self, request: PageRequest) -> PageDraft:
        self.requests.append(request)
        await anyio.sleep(0.2)  # long enough for both pages to be in flight together
        return replace(good_page(request), cost_usd=0.5)


@pytest.mark.parametrize("team", [False, True])
def test_pages_in_flight_never_take_a_budget_past_its_limit(paths: Paths, team: bool) -> None:
    """Two pages at a time, each spending its whole cap of 0.5: with 0.75 to spend, the second can't start."""
    file = paths.target_file("t")
    update_budget = 10.0 if team else 0.75
    file.write_text(file.read_text() + f"[generation]\nconcurrency = 2\nmax_budget_usd_per_call = 0.5\n"
                    f"max_budget_usd_per_update = {update_budget}\n")  # fmt: skip

    shared = SharedBudget(0.75) if team else None
    claude = SlowPages(plans=[PLAN])
    generation = run_update(paths, "t", claude=claude, shared_budget=shared).generation
    assert generation is not None
    assert generation.cost_usd == 0.5 and len(generation.written) == 1 and len(generation.left_for_later) == 1
    assert shared is None or shared.spent_usd == 0.5


def test_a_budget_smaller_than_one_calls_cap_runs_nothing(paths: Paths) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_budget_usd_per_update = 0.5\n")  # the cap is 1.0
    claude = FakeAssistant(plans=[PLAN], page_writer=good_page)
    with pytest.raises(CodetrailError, match="smaller than one call's cap"):
        run_update(paths, "t", claude=claude)
    assert claude.requests == []


PLAN_WITH_PATHS = PlanDraft(
    PLAN.pages,
    paths=[
        {"id": "paths/start-here", "title": "Start here", "goal": "Understand the API.",
         "steps": ["areas/api", "concepts/fastapi", "concepts/missing"]},
        {"id": "paths/empty", "title": "Nothing", "goal": "x", "steps": ["concepts/missing"]},
    ],
)  # fmt: skip


def test_paths_are_planned_validated_and_written(paths: Paths) -> None:
    generation = run_update(paths, "t", claude=FakeAssistant(plans=[PLAN_WITH_PATHS], page_writer=good_page)).generation
    assert generation is not None
    path = guide(paths).read_page("paths/start-here")
    assert path is not None and path.kind == "path"
    assert path.meta["steps"] == ["areas/api", "concepts/fastapi"]
    assert guide(paths).read_page("paths/empty") is None
    assert any("concepts/missing" in problem for problem in generation.outline_problems)


def test_an_outline_without_paths_gets_them_planned(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    outline = guide(paths).read_outline()
    assert outline is not None
    guide(paths).write_outline({"pages": outline["pages"]})  # as written before paths existed
    guide(paths).commit("An outline from before paths")
    claude = FakeAssistant(plans=[PlanDraft([], paths=PLAN_WITH_PATHS.paths)], page_writer=good_page)
    run_update(paths, "t", claude=claude)
    request = claude.requests[0]
    assert isinstance(request, PlanRequest) and request.paths_only
    assert guide(paths).read_page("paths/start-here") is not None


def test_each_calls_usage_is_recorded_and_counted(paths: Paths) -> None:
    def priced_page(request: PageRequest) -> PageDraft:
        draft = good_page(request)
        usage = Usage("claude_code", "claude-sonnet-5-5", 1000, 0, 100, 0.25)
        return PageDraft(draft.body, draft.checks, draft.files_read, 0.25, usage, brief=draft.brief)

    result = run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=priced_page))
    assert result.generation is not None and result.generation.cost_usd == 0.5
    connection = connect(paths.target_data("t") / "codetrail.db")
    rows = connection.execute("SELECT kind, provider, cost_usd FROM assistant_calls").fetchall()
    assert [tuple(row) for row in rows] == [("write", "claude_code", 0.25), ("write", "claude_code", 0.25)]


PAID = Usage("claude_code", "claude-sonnet-5-5", 1000, 0, 100, 0.9)


def call_rows(paths: Paths) -> list[tuple[str, float]]:
    connection = connect(paths.target_data("t") / "codetrail.db")
    try:
        return [tuple(row) for row in connection.execute("SELECT kind, cost_usd FROM assistant_calls")]
    finally:
        connection.close()


def test_a_failed_page_still_counts_what_it_cost(paths: Paths) -> None:
    one_at_a_time(paths, budget_usd=0.5, per_call_usd=0.5)
    failed = PaidCallFailed("Claude Code didn't finish the task (error_max_budget_usd).", PAID)
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [failed]}, page_writer=good_page)
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None
    assert generation.cost_usd == 0.9 and generation.tokens == 1100
    assert generation.failed == [("areas/api", str(failed))]
    assert generation.left_for_later == ["concepts/fastapi"]  # the failed call spent the budget: no second page
    assert ("write", 0.9) in call_rows(paths)


def test_a_failed_first_plan_still_counts_what_it_cost(paths: Paths) -> None:
    shared = SharedBudget(5.0)
    claude = FakeAssistant(plans=[PaidCallFailed("Claude Code didn't finish the task (error_max_turns).", PAID)])
    with pytest.raises(PaidCallFailed):
        run_update(paths, "t", claude=claude, shared_budget=shared)
    assert shared.spent_usd == 0.9
    assert call_rows(paths) == [("plan", 0.9)]


def test_a_failed_paths_plan_still_counts_what_it_cost(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    outline = guide(paths).read_outline()
    assert outline is not None
    guide(paths).write_outline({"pages": outline["pages"]})  # as written before paths existed
    guide(paths).commit("An outline from before paths")
    claude = FakeAssistant(plans=[PaidCallFailed("Claude Code didn't finish the task (error_max_turns).", PAID)])
    generation = run_update(paths, "t", claude=claude).generation
    assert generation is not None and generation.cost_usd == 0.9
    assert ("plan", 0.9) in call_rows(paths)


def test_a_failed_digest_still_counts_what_it_cost(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    add_commit(tmp_path / "target", {"services/api/app/routes.py": "x = 1\n"}, "feat(api): routes\n\nWhy: need")
    failed = PaidCallFailed("Claude Code didn't finish the task (error_max_turns).", PAID)
    generation = run_update(paths, "t", claude=FakeAssistant(page_writer=good_page, digests=[failed])).generation
    assert generation is not None and generation.digest is not None
    assert ("digest", 0.9) in call_rows(paths)
    digest = guide(paths).read_page(generation.digest)
    assert digest is not None and "commits since the last update" in digest.title


def one_at_a_time(paths: Paths, budget_usd: float = 10.0, per_call_usd: float = 1.0) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + f"[generation]\nconcurrency = 1\nmax_budget_usd_per_update = {budget_usd}\n"
                    f"max_budget_usd_per_call = {per_call_usd}\n")  # fmt: skip


def test_an_update_reports_each_step_as_it_goes(paths: Paths) -> None:
    one_at_a_time(paths)
    events: list[dict[str, object]] = []
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page), progress=events.append)
    pages = [event for event in events if event["step"] == "page"]
    assert [event.pop("reason") for event in pages] == ["new", "new"]
    assert all(int(str(event.pop("changed"))) > 0 for event in pages)  # every fact is new on the first update
    assert events[0] == {"step": "facts"}
    assert events[1]["step"] == "facts_recorded" and int(str(events[1]["changes"])) > 0
    no_usage = {"provider": "", "model": "", "tokens": 0}
    whole = {"revise": False, "unchanged": False}
    assert events[2:] == [
        {"step": "plan"},
        {"step": "planned", "pages": 2, **no_usage, "cost_usd": 0.0},
        {"step": "page", "id": "areas/api", "title": "The API", "attempt": 1, "revise": False},
        {"step": "page_written", "id": "areas/api", "title": "The API", **whole, **no_usage, "cost_usd": 0.01},
        {"step": "page", "id": "concepts/fastapi", "title": "Why FastAPI", "attempt": 1, "revise": False},
        {
            "step": "page_written",
            "id": "concepts/fastapi",
            "title": "Why FastAPI",
            **whole,
            **no_usage,
            "cost_usd": 0.01,
        },
        {"step": "digest"},
        {"step": "committed", "pages": 2, "failed": 0, "later": 0},
    ]


def test_the_steps_report_a_retry_a_failed_page_and_one_left_for_later(paths: Paths) -> None:
    one_at_a_time(paths, budget_usd=0.025, per_call_usd=0.01)  # room for one page and its retry, not a second page
    bad = PageDraft("Cites [[module:ghost.py]].", CHECKS, cost_usd=0.01, brief=BRIEF)
    events: list[dict[str, object]] = []
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": [bad, bad]}, page_writer=good_page)
    run_update(paths, "t", claude=claude, progress=events.append)
    steps = [(event["step"], event.get("attempt")) for event in events[4:]]
    assert steps == [("page", 1), ("page", 2), ("page_failed", None), ("page_later", None), ("committed", None)]
    failed = events[6]
    assert failed["title"] == "The API" and "ghost.py" in str(failed["reason"])
    assert events[7] == {"step": "page_later", "id": "concepts/fastapi", "title": "Why FastAPI"}
    assert events[-1] == {"step": "committed", "pages": 0, "failed": 1, "later": 1}


def test_a_written_page_keeps_its_brief(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    page = guide(paths).read_page("areas/api")
    assert page is not None and page.meta["brief"] == BRIEF


def test_a_page_without_a_brief_is_retried_with_the_problem(paths: Paths) -> None:
    request = PageRequest("areas/api", "area", "The API", ["services/api"], "", "", "")
    drafts: list[PageDraft | Exception] = [replace(good_page(request), brief=""), good_page(request)]
    claude = FakeAssistant(plans=[PLAN], pages={"areas/api": drafts}, page_writer=good_page)
    run_update(paths, "t", claude=claude)
    retry = [item for item in claude.requests if isinstance(item, PageRequest) and item.page_id == "areas/api"][1]
    assert "The brief is missing." in retry.problems
    assert retry.brief_words == 300


def update_with_digest(paths: Paths, tmp_path: Path, digest: DigestDraft) -> Page | None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    add_commit(tmp_path / "target", {"services/api/app/routes.py": "x = 1\n"}, "feat(api): routes\n\nWhy: need")
    generation = run_update(paths, "t", claude=FakeAssistant(page_writer=good_page, digests=[digest])).generation
    assert generation is not None and generation.digest is not None
    return guide(paths).read_page(generation.digest)


def test_digest_stores_highlights(paths: Paths, tmp_path: Path) -> None:
    page = update_with_digest(paths, tmp_path, DigestDraft("Routes", "Routes added.", highlights=["One.", "Two."]))
    assert page is not None and page.title == "Routes" and page.meta["highlights"] == ["One.", "Two."]


def test_digest_without_highlights_drops_returned_ones(paths: Paths, tmp_path: Path) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "config.toml").write_text("[team]\nhighlights_per_digest = 0\n")
    page = update_with_digest(paths, tmp_path, DigestDraft("Routes", "Routes added.", highlights=["One."]))
    assert page is not None and page.title == "Routes" and page.meta["highlights"] == []  # the digest itself stays


def guide_text(paths: Paths) -> str:
    folder = paths.target_data("t") / "guide"
    return "\n".join(file.read_text() for file in folder.rglob("*") if file.is_file() and ".git" not in file.parts)


def test_a_secret_in_a_planned_title_or_goal_never_reaches_the_guide(paths: Paths) -> None:
    token = fake_github_token(43)
    pages = [{**PLAN.pages[0], "title": f"The API, key {token}"}, PLAN.pages[1]]
    plan = PlanDraft(pages, paths=[{"id": "paths/start-here", "title": "Start here", "goal": f"Use {token}.",
                                    "steps": ["concepts/fastapi"]}])  # fmt: skip
    generation = run_update(paths, "t", claude=FakeAssistant(plans=[plan], page_writer=good_page)).generation
    assert generation is not None and generation.written == ["concepts/fastapi"]
    assert token not in guide_text(paths)
    flagged = [problem for problem in generation.outline_problems if "looks like a secret" in problem]
    assert len(flagged) == 2 and token not in " ".join(generation.outline_problems)


def test_a_digest_title_holding_a_secret_falls_back(paths: Paths, tmp_path: Path) -> None:
    token = fake_github_token(44)
    page = update_with_digest(paths, tmp_path, DigestDraft(f"Routes for {token}", "Routes added."))
    assert page is not None and "commits since the last update" in page.title
    assert token not in guide_text(paths)


def test_digest_with_bad_highlights_falls_back(paths: Paths, tmp_path: Path) -> None:
    draft = DigestDraft("Routes", "Routes added.", highlights=[f"Item {number}." for number in range(5)])
    page = update_with_digest(paths, tmp_path, draft)  # the default maximum is three
    assert page is not None and "commits since the last update" in page.title and "highlights" not in page.meta


def test_a_free_local_model_reserves_nothing(paths: Paths) -> None:
    """A local model costs nothing, so a budget below the per-call cap doesn't stop its update."""
    file = paths.target_file("t")
    file.write_text(file.read_text() + '[models]\nplan = "local:qwen3"\nwrite = "local:qwen3"\ndigest = "local:qwen3"\n'
                    "\n[generation]\nmax_budget_usd_per_update = 0.5\n")  # fmt: skip
    generation = run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page)).generation
    assert generation is not None and generation.written == ["areas/api", "concepts/fastapi"]


def test_an_update_with_pages_to_write_refuses_a_budget_below_one_calls_cap(paths: Paths, tmp_path: Path) -> None:
    """Not only a first outline: any update that would make a paid call refuses, before calling anything."""
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    add_commit(tmp_path / "target", {"services/api/app/routes.py": "from app import db\n"}, "feat(api): routes")
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_budget_usd_per_update = 0.5\n")  # the cap is 1.0
    claude = FakeAssistant(page_writer=good_page)
    with pytest.raises(CodetrailError, match="smaller than one call's cap"):
        run_update(paths, "t", claude=claude)
    assert claude.requests == []


class Stopped(BaseException):
    """Stands in for the update being cancelled, as when the server stops, while a call runs."""


class StoppedPage(FakeAssistant):
    async def write_page(self, request: PageRequest) -> PageDraft:
        self.requests.append(request)
        raise Stopped


def test_a_call_cancelled_while_it_runs_counts_its_cap(paths: Paths) -> None:
    """Its program ran and no result says what it used, so its cap counts, as for a call stopped at its time limit."""
    one_at_a_time(paths, per_call_usd=0.5)
    shared = SharedBudget(5.0)
    claude = StoppedPage(plans=[PLAN])
    with pytest.raises((Stopped, BaseExceptionGroup)):
        run_update(paths, "t", claude=claude, shared_budget=shared)
    started = sum(1 for request in claude.requests if isinstance(request, PageRequest))
    assert started >= 1 and shared.spent_usd == pytest.approx(0.5 * started)
    assert call_rows(paths).count(("write", 0.5)) == started
