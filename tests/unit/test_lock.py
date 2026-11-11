"""One update at a time per target (design section 10), and no removal while a target is in use."""

import errno
import fcntl
from pathlib import Path

import pytest

from codetrail.config import Paths, write_target
from codetrail.errors import CodetrailError
from codetrail.lock import (
    TargetBusy,
    TeamBusy,
    target_in_use,
    target_lock,
    target_removal,
    team_catch_up_lock,
)


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")


def test_a_second_holder_is_told_an_update_is_running(paths: Paths) -> None:
    with (
        target_lock(paths, "shop"),
        pytest.raises(CodetrailError, match="already updating"),
        target_lock(paths, "shop"),
    ):
        pass


def test_other_targets_are_independent(paths: Paths) -> None:
    with target_lock(paths, "shop"), target_lock(paths, "other"):
        pass


def test_the_lock_is_released_after_an_error(paths: Paths) -> None:
    with pytest.raises(RuntimeError), target_lock(paths, "shop"):
        raise RuntimeError("boom")
    with target_lock(paths, "shop"):
        pass


@pytest.fixture
def target(paths: Paths, tmp_path: Path) -> str:
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    write_target(paths, "shop", repository, "main")
    return "shop"


def test_commands_can_use_a_target_together(paths: Paths, target: str) -> None:
    with target_in_use(paths, target), target_in_use(paths, target):
        pass


def test_a_target_in_use_is_not_removed(paths: Paths, target: str) -> None:
    with (
        target_in_use(paths, target),
        pytest.raises(
            TargetBusy, match=r"in use: stop the Codetrail server serving it \(`codetrail serve` or `codetrail te"
        ),
        target_removal(paths, target),
    ):
        pass


def test_a_target_being_removed_is_not_used(paths: Paths, target: str) -> None:
    with target_removal(paths, target), pytest.raises(TargetBusy, match="being removed"), target_in_use(paths, target):
        pass


def test_the_in_use_lock_is_released_afterwards(paths: Paths, target: str) -> None:
    with target_in_use(paths, target):
        pass
    with target_removal(paths, target):
        pass


def test_an_unknown_target_is_refused_without_creating_folders(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="No target named 'shop'"), target_in_use(paths, "shop"):
        pass
    assert not paths.data_dir.exists()
    assert not paths.state_dir.exists()


def test_a_removal_that_finishes_while_opening_is_noticed(
    paths: Paths, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = fcntl.flock

    def removed_first(handle: object, operation: int) -> None:
        paths.target_file(target).unlink(missing_ok=True)
        lock(handle, operation)  # type: ignore[arg-type]

    monkeypatch.setattr(fcntl, "flock", removed_first)
    with pytest.raises(CodetrailError, match="No target named 'shop'"), target_in_use(paths, target):
        pass
    assert not paths.data_dir.exists()


def test_a_settings_path_that_cant_be_opened_is_reported(paths: Paths) -> None:
    paths.target_file("shop").mkdir(parents=True)
    with pytest.raises(CodetrailError, match="Can't lock target 'shop'"), target_in_use(paths, "shop"):
        pass


def test_a_filesystem_without_locks_is_reported(paths: Paths, target: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def unsupported(handle: object, operation: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", unsupported)
    with pytest.raises(CodetrailError, match="Can't lock target 'shop'"), target_removal(paths, target):
        pass


def test_a_team_catch_up_is_refused_while_another_runs(paths: Paths) -> None:
    with (
        team_catch_up_lock(paths, "acme"),
        pytest.raises(TeamBusy, match=r"Team 'acme' is already catching up\."),
        team_catch_up_lock(paths, "acme"),
    ):
        pass
    with team_catch_up_lock(paths, "acme"), team_catch_up_lock(paths, "other"):
        pass
