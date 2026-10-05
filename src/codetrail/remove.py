"""Removing a target: everything Codetrail keeps for it, never the repository it teaches (AGENTS.md, Never do, 6)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from codetrail.config import Paths, validate_target_name
from codetrail.errors import CodetrailError
from codetrail.lock import target_lock


def remove_target(paths: Paths, name: str, confirm: Callable[[list[Path]], bool]) -> bool:
    """Shows `confirm` the target's existing locations and deletes them if it agrees; returns whether it did.

    The settings file goes last, so a removal that fails part way leaves a target that can be removed again."""
    settings = paths.target_file(validate_target_name(name))
    if not settings.exists():
        raise CodetrailError(f"No target named {name!r}.")
    locations = [paths.target_data(name), paths.target_state(name), paths.ignore_file(name), settings]
    if not confirm([location for location in locations if _exists(location)]):
        return False
    with target_lock(paths, name):  # creates the data folder if it's missing, so all locations are checked again
        for location in locations:
            if location.is_dir() and not location.is_symlink():
                shutil.rmtree(location)  # never follows a symlink inside the folder
            elif _exists(location):
                location.unlink()  # a file, or a symlink: the link goes, never what it points to
    return True


def _exists(location: Path) -> bool:
    return location.is_symlink() or location.exists()
