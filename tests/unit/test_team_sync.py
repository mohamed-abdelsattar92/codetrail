"""Syncing a team: targets for newly followed repositories, and the ones that stopped (design 24.4 and 24.5)."""

from pathlib import Path

import pytest

from codetrail.config import Paths, TeamSource, load_target, load_team
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting
from codetrail.lock import team_removal
from codetrail.team.settings import save_selection, team_target_names, team_targets, write_team
from codetrail.team.sync import sync_team
from tests.unit.test_team_follow import NOW, repository


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")


def fake(source: str, repositories: list[HostedRepository]) -> FakeHosting:
    status = HostingStatus("github", installed=True, signed_in=True, account="reader", fix=None)
    return FakeHosting("github", [status], [Source("github", source, None)], {source: repositories}, {})


def team_following(paths: Paths, **changes: object) -> None:
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org", **changes))  # type: ignore[arg-type]


def test_a_selection_is_saved_and_replaced(paths: Paths) -> None:
    team_following(paths)
    save_selection(
        paths, "acme", TeamSource(service="github", source="acme-org", follow="chosen", chosen=["a"], clone_over="ssh")
    )
    save_selection(paths, "acme", TeamSource(service="gitlab", source="example-group"))

    sources = load_team(paths, "acme").sources

    assert [(found.service, found.source, found.follow, found.clone_over) for found in sources] == [
        ("github", "acme-org", "chosen", "ssh"),
        ("gitlab", "example-group", "all", "https"),
    ]
    assert sources[0].chosen == ["a"]
    assert not list(paths.team_file("acme").parent.glob("*.tmp"))


def test_save_selection_keeps_models_and_catch_up(paths: Paths) -> None:
    team_following(paths)
    file = paths.team_file("acme")
    file.write_text(
        file.read_text(encoding="utf-8") + '\n[models]\noverview = "local:qwen3"\n\n'
        "[catch_up]\nmax_budget_usd = 5.5\nconcurrency = 3\nmax_budget_usd_per_call = 0.1\n",
        encoding="utf-8",
    )
    before = load_team(paths, "acme")

    save_selection(paths, "acme", TeamSource(service="gitlab", source="example-group"))

    team = load_team(paths, "acme")
    assert len(team.sources) == 2
    assert team.models == before.models
    assert team.catch_up == before.catch_up  # every field: a dropped one would quietly go back to its default


def test_save_selection_keeps_a_lone_catch_up_setting(paths: Paths) -> None:
    team_following(paths)
    file = paths.team_file("acme")
    file.write_text(
        file.read_text(encoding="utf-8") + "\n[catch_up]\nmax_budget_usd_per_call = 0.2\n", encoding="utf-8"
    )

    save_selection(paths, "acme", TeamSource(service="gitlab", source="example-group"))

    assert load_team(paths, "acme").catch_up.max_budget_usd_per_call == 0.2


def test_save_selection_writes_no_default_tables(paths: Paths) -> None:
    team_following(paths)
    text = paths.team_file("acme").read_text(encoding="utf-8")
    assert "[models]" not in text
    assert "[catch_up]" not in text


def test_writing_a_team_twice_is_refused(paths: Paths) -> None:
    write_team(paths, "acme")
    with pytest.raises(CodetrailError, match="already exists"):
        write_team(paths, "acme")


def test_sync_writes_targets_and_reports_stopped(paths: Paths) -> None:
    team_following(paths, clone_over="ssh")
    listed = [repository("a1", days_since_push=1), repository("b2", days_since_push=1)]
    first = sync_team(paths, "acme", {"github": fake("acme-org", listed)}, NOW, branches={"b2": "develop"})

    assert first.added == ["acme-repo-a1", "acme-repo-b2"]
    assert first.stopped == [] and first.unreachable == []
    target = load_target(paths, "acme-repo-b2")
    assert (target.team, target.branch) == ("acme", "develop")
    assert target.remote is not None and target.remote.url == "git@github.com:acme-org/repo-b2.git"
    assert load_target(paths, "acme-repo-a1").branch == "main"

    later = [repository("a1", archived=True)]  # b2 left the source, a1 was archived
    second = sync_team(paths, "acme", {"github": fake("acme-org", later)}, NOW)

    assert second.added == []
    assert second.stopped == ["acme-repo-a1", "acme-repo-b2"]


def test_sync_matches_a_renamed_repository_by_id(paths: Paths) -> None:
    team_following(paths)
    sync_team(paths, "acme", {"github": fake("acme-org", [repository("a1")])}, NOW)
    renamed = repository("a1")
    renamed = HostedRepository(**{**renamed.__dict__, "name": "renamed"})

    result = sync_team(paths, "acme", {"github": fake("acme-org", [renamed])}, NOW)

    assert result.added == []
    assert list(team_targets(paths, "acme")) == ["acme-repo-a1"]


def test_a_name_clash_gets_the_start_of_the_id(paths: Paths) -> None:
    team_following(paths)
    twin = HostedRepository(**{**repository("zz9876xyz").__dict__, "name": "repo-a1"})

    result = sync_team(paths, "acme", {"github": fake("acme-org", [repository("a1"), twin])}, NOW)

    assert result.added == ["acme-repo-a1", "acme-repo-a1-zz9876"]


def test_a_third_repository_with_the_same_name_gets_a_free_name(paths: Paths) -> None:
    # GitHub ids start alike, so the start of the id doesn't tell three repositories apart.
    team_following(paths)
    listed = [
        HostedRepository(**{**repository(identifier).__dict__, "name": name})
        for identifier, name in (("R_kgDOAAA1", "x.y"), ("R_kgDOAAA2", "x-y"), ("R_kgDOAAA3", "x_y"))
    ]

    result = sync_team(paths, "acme", {"github": fake("acme-org", listed)}, NOW)

    assert result.added == ["acme-x-y", "acme-x-y-rkgdoa", "acme-x-y-rkgdoa-2"]
    assert {target.remote.id for target in team_targets(paths, "acme").values() if target.remote} == {
        "R_kgDOAAA1",
        "R_kgDOAAA2",
        "R_kgDOAAA3",
    }


def test_one_unreachable_source_does_not_stop_the_others(paths: Paths) -> None:
    team_following(paths)
    save_selection(paths, "acme", TeamSource(service="gitlab", source="example-group"))
    status = HostingStatus("gitlab", installed=True, signed_in=False, account=None, fix=None)
    broken = FakeHosting("gitlab", [status], [], {}, {})  # has no source "example-group": raises HostingError

    result = sync_team(paths, "acme", {"github": fake("acme-org", [repository("a1")]), "gitlab": broken}, NOW)

    assert result.added == ["acme-repo-a1"]
    assert result.unreachable == ["gitlab:example-group"]


def test_sync_refuses_while_the_team_is_being_removed(paths: Paths) -> None:
    team_following(paths)
    with team_removal(paths, "acme"), pytest.raises(CodetrailError, match="being removed"):
        sync_team(paths, "acme", {"github": fake("acme-org", [])}, NOW)


def test_team_targets_are_those_of_the_team(paths: Paths) -> None:
    team_following(paths)
    sync_team(paths, "acme", {"github": fake("acme-org", [repository("a1")])}, NOW)
    paths.target_file("local").write_text('repository = "/somewhere"\nbranch = "main"\n')

    assert list(team_targets(paths, "acme")) == ["acme-repo-a1"]


def test_a_service_that_is_missing_makes_its_source_unreachable(paths: Paths) -> None:
    team_following(paths)

    assert sync_team(paths, "acme", {}, NOW).unreachable == ["github:acme-org"]


def test_a_team_file_with_characters_outside_the_bmp_round_trips(paths: Paths) -> None:
    write_team(paths, "acme")
    selection = TeamSource(service="azure_devops", source="contoso/Platform 🚀", left_out=["a1"])

    save_selection(paths, "acme", selection)

    assert load_team(paths, "acme").sources == [selection]


def test_names_that_are_not_target_names_are_skipped(paths: Paths) -> None:
    team_following(paths)
    paths.target_file("x").parent.mkdir(parents=True, exist_ok=True)
    (paths.target_file("x").parent / "Not A Name.toml").write_text('team = "acme"\nbranch = "main"\n')

    assert team_target_names(paths, "acme") == []
