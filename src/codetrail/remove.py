"""Removing a target or a team: everything Codetrail keeps for it, never what it teaches (AGENTS.md, Never do, 6)."""

from __future__ import annotations

import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from codetrail.config import Paths, check_containment, load_team, validate_target_name, validate_team_name
from codetrail.errors import CodetrailError
from codetrail.lock import existing_target_lock, target_removal, team_removal
from codetrail.team.follow import follows
from codetrail.team.settings import team_target_names


def remove_target(paths: Paths, name: str, confirm: Callable[[list[Path]], bool]) -> bool:
    """Shows `confirm` the target's existing locations and deletes them if it agrees; returns whether it did.

    The settings file goes last, and its lock is held until then: a removal that fails part way leaves a target
    that can be removed again, and no command can start using the target meanwhile. A target its team still follows
    is refused, since following the source would add it back."""
    settings = paths.target_file(validate_target_name(name))
    if not settings.exists():
        raise CodetrailError(f"No target named {name!r}.")
    settings_data = _settings_data(settings)
    team = _following_team(paths, settings_data)
    if team is not None:
        raise CodetrailError(
            f"Target {name!r} is followed by team {team!r}, which would add it back. "
            f"Leave its repository out of the team first."
        )
    locations = _target_locations(paths, name, settings_data)
    if not confirm([location for location in locations if _exists(location)]):
        return False
    _delete_target(paths, name, locations)
    return True


def remove_team(paths: Paths, name: str, confirm: Callable[[list[Path]], bool]) -> bool:
    """Shows `confirm` the team's file, its data and each of its targets' locations, and deletes them if it agrees.

    The targets go first (the whole team is going, so being followed doesn't stop them), then the team's data and
    file. Nothing on the service is touched."""
    team_file = paths.team_file(validate_team_name(name))
    if not team_file.exists():
        raise CodetrailError(f"No team named {name!r}.")
    targets = {target: _target_locations(paths, target, _settings_data(paths.target_file(target)))
               for target in team_target_names(paths, name)}  # fmt: skip
    team_locations = [team_file, paths.team_data(name)]
    every_location = team_locations + [location for locations in targets.values() for location in locations]
    if not confirm([location for location in every_location if _exists(location)]):
        return False
    with team_removal(paths, name):  # refused while `team serve` or a sync uses the team
        for target, locations in targets.items():
            _delete_target(paths, target, locations)
        for location in team_locations:
            _delete(location)
    return True


def _delete_target(paths: Paths, name: str, locations: list[Path]) -> None:
    # Refused while serve, update or files uses the target, or an update runs.
    with target_removal(paths, name), existing_target_lock(paths, name):
        for location in locations:
            _delete(location)


def _delete(location: Path) -> None:
    if location.is_dir() and not location.is_symlink():
        shutil.rmtree(location)  # never follows a symlink inside the folder
    elif _exists(location):
        location.unlink()  # a file, or a symlink: the link goes, never what it points to


def _target_locations(paths: Paths, name: str, settings_data: dict[str, Any]) -> list[Path]:
    repository = settings_data.get("repository")
    if isinstance(repository, str):  # the XDG variables may have moved Codetrail's folders since the target was added
        check_containment(paths, Path(repository))
    settings = paths.target_file(name)
    return [paths.target_data(name), paths.target_state(name), paths.ignore_file(name), settings]


def _exists(location: Path) -> bool:
    return location.is_symlink() or location.exists()


def _settings_data(settings: Path) -> dict[str, Any]:
    """The target's settings as written; a broken file mustn't stop the target being removed."""
    try:
        return tomllib.loads(settings.read_text(encoding="utf-8"))
    except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
        return {}


def _following_team(paths: Paths, settings_data: dict[str, Any]) -> str | None:
    """The team that still follows the target's repository, if any; a team that doesn't load follows nothing."""
    team, remote = settings_data.get("team"), settings_data.get("remote")
    if not isinstance(team, str) or not isinstance(remote, dict):
        return None
    try:
        config = load_team(paths, team)
    except CodetrailError:
        return None
    return (
        team if follows(config, str(remote.get("service")), str(remote.get("source")), str(remote.get("id"))) else None
    )
