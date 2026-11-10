"""Removing a target deletes everything Codetrail keeps for it, and nothing else."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from codetrail.config import Paths, TeamSource, write_target
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting
from codetrail.lock import target_in_use, target_lock, team_in_use
from codetrail.remove import remove_target, remove_team
from codetrail.team.settings import save_selection, write_team
from codetrail.team.sync import sync_team
from tests.unit.test_team_follow import NOW, repository


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")


def fake_service(repositories: list[HostedRepository]) -> FakeHosting:
    status = HostingStatus("github", installed=True, signed_in=True, account="reader", fix=None)
    return FakeHosting("github", [status], [Source("github", "acme-org", None)], {"acme-org": repositories}, {})


def add(paths: Paths, tmp_path: Path, name: str) -> list[Path]:
    """Registers a target and fills each of its locations; returns them in the order they're listed."""
    repository = tmp_path / f"{name}-repository"
    (repository / ".git").mkdir(parents=True)
    write_target(paths, name, repository, "main")
    paths.ignore_file(name).write_text("vendor/\n")
    (paths.target_data(name) / "guide").mkdir(parents=True)
    (paths.target_data(name) / "codetrail.db").write_text("")
    paths.target_state(name).mkdir(parents=True)
    return [paths.target_data(name), paths.target_state(name), paths.ignore_file(name), paths.target_file(name)]


def agree_and_record(shown: list[Path]) -> Callable[[list[Path]], bool]:
    def confirm(found: list[Path]) -> bool:
        shown.extend(found)
        return True

    return confirm


def test_removes_every_location_and_nothing_else(paths: Paths, tmp_path: Path) -> None:
    locations = add(paths, tmp_path, "shop")
    others = add(paths, tmp_path, "other")
    shown: list[Path] = []

    assert remove_target(paths, "shop", agree_and_record(shown))

    assert shown == locations
    assert not any(location.exists() for location in locations)
    assert all(location.exists() for location in others)
    assert (tmp_path / "shop-repository" / ".git").is_dir()


def test_only_existing_locations_are_listed(paths: Paths, tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    write_target(paths, "shop", repository, "main")
    shown: list[Path] = []

    assert remove_target(paths, "shop", agree_and_record(shown))

    assert shown == [paths.target_file("shop")]
    assert not paths.target_file("shop").exists()


def test_declining_keeps_everything(paths: Paths, tmp_path: Path) -> None:
    locations = add(paths, tmp_path, "shop")

    assert not remove_target(paths, "shop", lambda found: False)

    assert all(location.exists() for location in locations)


def test_an_unknown_target_is_refused(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="No target named 'shop'"):
        remove_target(paths, "shop", lambda found: True)


@pytest.mark.parametrize("name", ["../config", "..", "x/y", ""])
def test_invalid_names_are_refused_before_anything_is_touched(paths: Paths, tmp_path: Path, name: str) -> None:
    locations = add(paths, tmp_path, "shop")
    with pytest.raises(CodetrailError, match="isn't a valid target name"):
        remove_target(paths, name, lambda found: True)
    assert all(location.exists() for location in locations)


def test_a_symlinked_folder_is_unlinked_not_followed(paths: Paths, tmp_path: Path) -> None:
    add(paths, tmp_path, "shop")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("keep")
    for folder in (paths.target_data("shop"), paths.target_state("shop")):
        shutil.rmtree(folder)
        folder.symlink_to(elsewhere, target_is_directory=True)

    assert remove_target(paths, "shop", lambda found: True)

    assert not paths.target_data("shop").is_symlink()
    assert not paths.target_state("shop").is_symlink()
    assert [child.name for child in elsewhere.iterdir()] == ["keep.txt"]  # the lock wasn't taken through the link
    assert (elsewhere / "keep.txt").read_text() == "keep"


def test_a_dangling_symlinked_data_folder_is_removed(paths: Paths, tmp_path: Path) -> None:
    add(paths, tmp_path, "shop")
    shutil.rmtree(paths.target_data("shop"))
    paths.target_data("shop").symlink_to(tmp_path / "gone", target_is_directory=True)

    assert remove_target(paths, "shop", lambda found: True)

    assert not paths.target_data("shop").is_symlink()
    assert not paths.target_file("shop").exists()


def test_folders_inside_the_repository_are_never_removed(paths: Paths, tmp_path: Path) -> None:
    """The XDG variables may have changed since the target was added; the repository is checked again."""
    add(paths, tmp_path, "shop")
    repository = tmp_path / "shop-repository"
    (repository / "codetrail" / "shop").mkdir(parents=True)
    (repository / "codetrail" / "shop" / "keep.txt").write_text("keep")
    moved = Paths(config_dir=paths.config_dir, data_dir=repository / "codetrail", state_dir=paths.state_dir)
    asked: list[Path] = []

    with pytest.raises(CodetrailError, match="can't be inside the repository"):
        remove_target(moved, "shop", agree_and_record(asked))

    assert asked == []
    assert (repository / "codetrail" / "shop" / "keep.txt").read_text() == "keep"
    assert moved.target_file("shop").exists()


def test_a_target_in_use_is_not_removed(paths: Paths, tmp_path: Path) -> None:
    locations = add(paths, tmp_path, "shop")
    with target_in_use(paths, "shop"), pytest.raises(CodetrailError, match="in use"):
        remove_target(paths, "shop", lambda found: True)
    assert all(location.exists() for location in locations)


def test_an_updating_target_is_not_removed_even_if_its_settings_were_replaced(paths: Paths, tmp_path: Path) -> None:
    """An editor that renames a new settings file into place leaves the update's in-use lock on the old one."""
    locations = add(paths, tmp_path, "shop")
    with target_lock(paths, "shop"), pytest.raises(CodetrailError, match="already updating"):
        remove_target(paths, "shop", lambda found: True)
    assert all(location.exists() for location in locations)


def test_a_target_whose_settings_no_longer_load_can_still_be_removed(paths: Paths, tmp_path: Path) -> None:
    locations = add(paths, tmp_path, "shop")
    paths.target_file("shop").write_text("not = [valid toml\n")

    assert remove_target(paths, "shop", lambda found: True)

    assert not any(location.exists() for location in locations)


def test_team_remove_lists_asks_and_removes_everything(paths: Paths, tmp_path: Path) -> None:
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
    sync_team(paths, "acme", {"github": fake_service([repository("a1")])}, NOW)
    paths.team_data("acme").mkdir(parents=True)
    (paths.team_data("acme") / "team.db").write_text("")
    target_data = paths.target_data("acme-repo-a1")
    target_data.mkdir(parents=True)
    other = add(paths, tmp_path, "other")
    asked: list[list[Path]] = []

    def confirm(found: list[Path]) -> bool:
        asked.append(found)
        return True

    assert remove_team(paths, "acme", confirm)

    assert len(asked) == 1
    assert asked[0] == [
        paths.team_file("acme"),
        paths.team_data("acme"),
        target_data,
        paths.target_file("acme-repo-a1"),
    ]
    assert not any(location.exists() for location in asked[0])
    assert all(location.exists() for location in other)


def test_team_remove_does_nothing_when_declined(paths: Paths) -> None:
    write_team(paths, "acme")
    assert not remove_team(paths, "acme", lambda found: False)
    assert paths.team_file("acme").exists()


def test_team_remove_refuses_while_served(paths: Paths) -> None:
    write_team(paths, "acme")
    with team_in_use(paths, "acme"), pytest.raises(CodetrailError, match="in use"):
        remove_team(paths, "acme", lambda found: True)
    assert paths.team_file("acme").exists()


def test_target_remove_refuses_while_the_team_follows_it(paths: Paths) -> None:
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
    sync_team(paths, "acme", {"github": fake_service([repository("a1")])}, NOW)

    with pytest.raises(CodetrailError, match="team 'acme'"):
        remove_target(paths, "acme-repo-a1", lambda found: True)

    assert paths.target_file("acme-repo-a1").exists()


def test_target_remove_works_once_left_out(paths: Paths) -> None:
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
    sync_team(paths, "acme", {"github": fake_service([repository("a1")])}, NOW)
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org", left_out=["a1"]))

    assert remove_target(paths, "acme-repo-a1", lambda found: True)

    assert not paths.target_file("acme-repo-a1").exists()


def test_target_remove_works_when_the_team_is_gone(paths: Paths) -> None:
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
    sync_team(paths, "acme", {"github": fake_service([repository("a1")])}, NOW)
    paths.team_file("acme").unlink()

    assert remove_target(paths, "acme-repo-a1", lambda found: True)
