"""A catch-up of real team targets, each fetched from a repository served on 127.0.0.1, with the fake assistant.

Each target's update runs as it does alone, with its own lock and commit; the team shares one budget and gets one
overview of the digests this catch-up wrote (design sections 24.8 and 24.9).
"""

from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from codetrail.assistant import OverviewRequest, PageDraft, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths, RemoteSettings, TeamSource, write_remote_target
from codetrail.database import connect
from codetrail.guide import GuideRepository
from codetrail.team.catch_up import CatchUpEstimate, run_catch_up
from codetrail.team.settings import save_selection, write_team
from codetrail.team.state import Overviews, TeamState, team_database
from codetrail.update import run_update
from tests.fixtures.remote import allow_http_remotes, publish, serving_repository
from tests.fixtures.repos import Commit, add_commit, make_repository

FILES: Commit = {
    "services/api/pyproject.toml": '[project]\nname = "api"\ndependencies = ["fastapi==0.1"]\n',
    "services/api/app/__init__.py": "",
    "services/api/app/main.py": "from fastapi import FastAPI\n",
}
PLAN = PlanDraft(
    [
        {"id": "areas/api", "kind": "area", "title": "The API", "scope_paths": ["services/api"], "scope_kinds": [],
         "facts": ["project:services/api"]},
    ]
)  # fmt: skip
CHECKS = [
    {
        "id": "q1",
        "question": "What is the API?",
        "rubric": [{"point": "A service", "grounds": ["project:services/api"]}],
    }
]


def page_costing(cost_usd: float) -> PageDraft:
    return PageDraft("# The API\n\nIt uses [[project:services/api]].\n", CHECKS, [], cost_usd, brief="The API.")


def writer(cost_usd: float = 0.01) -> FakeAssistant:
    return FakeAssistant(plans=[PLAN], page_writer=lambda request: page_costing(cost_usd))


def tick_all(estimate: CatchUpEstimate) -> Collection[str]:
    return [line.target for line in estimate.lines]


@dataclass
class Team:
    paths: Paths
    checkout: Path
    served: Path
    url: str

    def add(self, target: str, identifier: str, repository: str, url: str | None = None) -> None:
        remote = RemoteSettings(service="github", source="acme-org", id=identifier, name=repository,
                                url=url or self.url)  # fmt: skip
        write_remote_target(self.paths, target, remote, "develop", "acme")

    def guide(self, target: str) -> GuideRepository:
        return GuideRepository(self.paths.target_data(target) / "guide")


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    """Team acme follows all of acme-org; every target fetches from one repository served on 127.0.0.1."""
    allow_http_remotes(monkeypatch, tmp_path)
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "checkout", [FILES])
    with serving_repository(checkout, tmp_path / "served") as url:
        write_team(paths, "acme")
        save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
        yield Team(paths, checkout, tmp_path / "served", url)


def set_catch_up(paths: Paths, budget_usd: float, concurrency: int) -> None:
    file = paths.team_file("acme")
    file.write_text(file.read_text() + f"\n[catch_up]\nmax_budget_usd = {budget_usd}\nconcurrency = {concurrency}\n")


def test_catch_up_one_target_fails_others_commit(team: Team) -> None:
    team.add("acme-web-shop", "R_1", "web-shop")
    team.add("acme-payments", "R_2", "payments", url=team.url.replace("served.git", "missing.git"))

    result = run_catch_up(team.paths, "acme", tick_all, writer_for=lambda target: writer(),
                          overview_writer=FakeAssistant())  # fmt: skip

    assert result.estimate is not None
    assert [line.target for line in result.estimate.lines] == ["acme-web-shop"]
    [(repository, reason)] = result.failed
    assert repository == "payments" and reason
    assert result.updated == ["acme-web-shop"]
    guide = team.guide("acme-web-shop")
    assert guide.read_page("areas/api") is not None and not guide.has_uncommitted_changes()
    assert result.overview is None  # one digest: nothing to connect


def test_catch_up_writes_overview(team: Team) -> None:
    team.add("acme-web-shop", "R_1", "web-shop")
    team.add("acme-payments", "R_2", "payments")
    for target in ("acme-web-shop", "acme-payments"):  # each has a guide already
        run_update(team.paths, target, claude=writer(), confirm=lambda estimate: True)
    add_commit(team.checkout, {"README.md": "The shop.\n"}, "docs: describe the shop")
    publish(team.checkout, team.served)
    overview_writer = FakeAssistant()

    result = run_catch_up(team.paths, "acme", tick_all, writer_for=lambda target: writer(),
                          overview_writer=overview_writer)  # fmt: skip

    assert result.estimate is not None and result.estimate.overview is not None
    assert all(line.digest_expected and not line.first_guide for line in result.estimate.lines)
    assert result.overview is not None and result.overview.overview_id is not None, result.overview
    [request] = overview_writer.requests
    assert isinstance(request, OverviewRequest)
    assert sorted(digest.repository for digest in request.digests) == ["payments", "web-shop"]
    record = TeamState(connect(team_database(team.paths, "acme"))).last_finished()
    assert record is not None and record.overview_id == result.overview.overview_id
    assert sorted(record.targets) == ["acme-payments", "acme-web-shop"]
    overviews = Overviews(team.paths.team_data("acme") / "overviews")
    assert overviews.read(record.overview_id) is not None
    assert not overviews.repository.has_uncommitted_changes()


def test_catch_up_team_budget(team: Team) -> None:
    team.add("acme-web-shop", "R_1", "web-shop")
    team.add("acme-payments", "R_2", "payments")
    set_catch_up(team.paths, budget_usd=0.5, concurrency=1)  # one at a time, so the first spends it all

    result = run_catch_up(team.paths, "acme", tick_all, writer_for=lambda target: writer(cost_usd=0.5),
                          overview_writer=FakeAssistant())  # fmt: skip

    assert len(result.updated) == 1
    [(repository, reason)] = result.failed
    assert "team's budget" in reason
    [written] = result.updated
    [not_written] = {"acme-web-shop", "acme-payments"} - {written}
    assert team.guide(written).read_page("areas/api") is not None
    assert team.guide(not_written).read_page("areas/api") is None
    assert repository == {"acme-web-shop": "web-shop", "acme-payments": "payments"}[not_written]


def test_catch_up_team_budget_stops_targets_with_guides(team: Team) -> None:
    team.add("acme-web-shop", "R_1", "web-shop")
    team.add("acme-payments", "R_2", "payments")
    for target in ("acme-web-shop", "acme-payments"):  # each has a guide already
        run_update(team.paths, target, claude=writer(), confirm=lambda estimate: True)
    heads = {target: team.guide(target).head() for target in ("acme-web-shop", "acme-payments")}
    upgraded = '[project]\nname = "api"\ndependencies = ["fastapi==0.2"]\n'  # a changed fact: the page is rewritten
    add_commit(team.checkout, {"services/api/pyproject.toml": upgraded}, "build: upgrade fastapi")
    publish(team.checkout, team.served)
    set_catch_up(team.paths, budget_usd=0.5, concurrency=1)  # one at a time, so the first spends it all

    result = run_catch_up(team.paths, "acme", tick_all, writer_for=lambda target: writer(cost_usd=0.5),
                          overview_writer=FakeAssistant())  # fmt: skip

    [written] = result.updated
    [not_written] = {"acme-web-shop", "acme-payments"} - {written}
    repository = {"acme-web-shop": "web-shop", "acme-payments": "payments"}[not_written]
    assert result.failed == [(repository, "The team's budget is spent, so this repository wasn't updated.")]
    assert team.guide(not_written).head() == heads[not_written]
    assert team.guide(written).head() != heads[written]
    record = TeamState(connect(team_database(team.paths, "acme"))).last_finished()
    assert record is not None and not_written in record.failed
