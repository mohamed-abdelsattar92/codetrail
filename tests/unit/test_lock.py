"""One update at a time per target (design section 10)."""

from pathlib import Path

import pytest

from codetrail.config import Paths
from codetrail.errors import CodetrailError
from codetrail.lock import target_lock


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
