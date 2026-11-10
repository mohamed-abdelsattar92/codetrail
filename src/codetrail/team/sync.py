"""Making a team's targets match its sources (design sections 24.4 and 24.5)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from codetrail.config import Paths, RemoteSettings, load_global, load_team, write_remote_target
from codetrail.errors import CodetrailError
from codetrail.hosting import HostingService
from codetrail.hosting.names import Service
from codetrail.lock import team_in_use
from codetrail.team.follow import followed, follows
from codetrail.team.names import team_target_name
from codetrail.team.settings import existing_target_names, team_targets

logger = logging.getLogger(__name__)


@dataclass
class TeamSync:
    added: list[str] = field(default_factory=list)  # names of the targets written
    stopped: list[str] = field(default_factory=list)  # names of followed targets whose repository is archived or gone
    unreachable: list[str] = field(default_factory=list)  # `<service>:<source>` of each source that couldn't be listed


def sync_team(
    paths: Paths,
    name: str,
    services: Mapping[Service, HostingService],
    now: datetime,
    branches: Mapping[str, str] = {},
) -> TeamSync:
    """Lists each source and writes a remote target for every newly followed repository.

    `branches` is by repository id; a repository not in it is taught on its default branch."""
    result = TeamSync()
    with team_in_use(paths, name):
        team = load_team(paths, name)
        active_days = load_global(paths).team.active_days
        existing = team_targets(paths, name)
        target_by_id = {target.remote.id: target_name for target_name, target in existing.items() if target.remote}
        taken = set(existing_target_names(paths))
        for selection in team.sources:
            label = f"{selection.service}:{selection.source}"
            try:
                if selection.service not in services:
                    raise CodetrailError(f"No hosting service for {selection.service}.")
                listed = services[selection.service].repositories(selection.source)
            except CodetrailError:
                logger.warning("Couldn't list the source %s.", label)
                result.unreachable.append(label)
                continue
            in_source = {
                target.remote.id: target_name
                for target_name, target in existing.items()
                if target.remote
                and (target.remote.service, target.remote.source) == (selection.service, selection.source)
            }
            for repository in followed(selection, listed, set(in_source), now, active_days):
                branch = branches.get(repository.id, repository.default_branch)
                if repository.id in target_by_id or branch is None:
                    continue
                target_name = team_target_name(name, repository.name, taken, repository.id)
                remote = RemoteSettings(
                    service=selection.service,
                    source=selection.source,
                    id=repository.id,
                    name=repository.name,
                    url=repository.clone_url(selection.clone_over),
                )
                write_remote_target(paths, target_name, remote, branch, name)
                taken.add(target_name)
                result.added.append(target_name)
            usable = {repository.id for repository in listed if not repository.archived}
            result.stopped.extend(
                target_name
                for repository_id, target_name in in_source.items()
                if repository_id not in usable and follows(team, selection.service, selection.source, repository_id)
            )
    return result
