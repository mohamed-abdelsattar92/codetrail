"""A team's settings file and the targets it created (design section 24.5)."""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from pathlib import Path

from codetrail.config import (
    TARGET_NAME,
    CatchUpSettings,
    Paths,
    TargetConfig,
    TeamModels,
    TeamSource,
    load_target,
    load_team,
    toml_string,
    validate_team_name,
)
from codetrail.errors import CodetrailError
from codetrail.lock import team_in_use

logger = logging.getLogger(__name__)


def write_team(paths: Paths, name: str) -> Path:
    """Creates the team's empty settings file and returns its path."""
    file = paths.team_file(validate_team_name(name))
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open("x", encoding="utf-8") as handle:
            handle.write(f"# Codetrail team {name!r}, written by `codetrail team add`.\n")
    except FileExistsError as error:
        raise CodetrailError(f"A team named {name!r} already exists: {file}") from error
    return file


def save_selection(paths: Paths, name: str, selection: TeamSource) -> None:
    """Replaces the team's entry for the selection's service and source, or adds it."""
    file = paths.team_file(validate_team_name(name))
    with team_in_use(paths, name):  # the lock also holds across the rename below
        _write_sources(paths, name, file, selection)


def _write_sources(paths: Paths, name: str, file: Path, selection: TeamSource) -> None:
    team = load_team(paths, name)
    sources = [
        entry for entry in team.sources if (entry.service, entry.source) != (selection.service, selection.source)
    ]
    sources.append(selection)
    text = "".join(_source_text(entry) for entry in sources) + _models_text(team.models) + _catch_up_text(team.catch_up)
    # A file in the same folder, renamed into place: a reader never sees half a file.
    descriptor, temporary = tempfile.mkstemp(dir=file.parent, prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"# Codetrail team {name!r}.\n{text}")
        os.replace(temporary, file)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _source_text(source: TeamSource) -> str:
    def array(values: list[str]) -> str:
        return "[" + ", ".join(toml_string(value) for value in values) + "]"

    return (
        "\n[[sources]]\n"
        f"service = {toml_string(source.service)}\n"
        f"source = {toml_string(source.source)}\n"
        f"follow = {toml_string(source.follow)}\n"
        f"clone_over = {toml_string(source.clone_over)}\n"
        f"left_out = {array(source.left_out)}\n"
        f"chosen = {array(source.chosen)}\n"
    )


def _models_text(models: TeamModels) -> str:
    if models == TeamModels():
        return ""
    return f"\n[models]\noverview = {toml_string(models.overview)}\n"


def _catch_up_text(catch_up: CatchUpSettings) -> str:
    if catch_up == CatchUpSettings():
        return ""
    return f"\n[catch_up]\nmax_budget_usd = {catch_up.max_budget_usd!r}\nconcurrency = {catch_up.concurrency}\n"


def existing_target_names(paths: Paths) -> list[str]:
    return sorted(
        file.stem for file in paths.target_file("x").parent.glob("*.toml") if TARGET_NAME.fullmatch(file.stem)
    )


def team_target_names(paths: Paths, name: str) -> list[str]:
    """The targets whose settings say they belong to the team, even those that no longer load fully."""
    names = []
    for target in existing_target_names(paths):
        try:
            data = tomllib.loads(paths.target_file(target).read_text(encoding="utf-8"))
        except OSError, UnicodeDecodeError, tomllib.TOMLDecodeError:
            continue
        if data.get("team") == name:
            names.append(target)
    return names


def team_targets(paths: Paths, name: str) -> dict[str, TargetConfig]:
    """Each target of the team, by name; one whose settings don't load is left out and logged."""
    targets: dict[str, TargetConfig] = {}
    for target in team_target_names(paths, name):
        try:
            targets[target] = load_target(paths, target)
        except CodetrailError:
            logger.warning("Left out target %r of team %r: its settings don't load.", target, name)
    return targets


def team_names(paths: Paths) -> list[str]:
    return sorted(file.stem for file in paths.team_file("x").parent.glob("*.toml"))
