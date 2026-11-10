"""Which of a source's repositories a team follows (design section 24.5)."""

from __future__ import annotations

from datetime import datetime, timedelta

from codetrail.config import TeamConfig, TeamSource
from codetrail.hosting import HostedRepository


def followed(
    source: TeamSource, listed: list[HostedRepository], already: set[str], now: datetime, active_days: int
) -> list[HostedRepository]:
    """The listed repositories the team follows; `already` holds the ids it follows now.

    Archived, disabled and empty repositories are never followed. `chosen` follows the chosen ones; `all` follows
    the active ones and keeps one that has gone quiet. A repository whose service reports no push time (Azure DevOps)
    counts as active."""
    usable = [
        repository
        for repository in listed
        if not (repository.archived or repository.disabled or repository.default_branch is None)
    ]
    if source.follow == "chosen":
        return [repository for repository in usable if repository.id in source.chosen]
    earliest = now - timedelta(days=active_days)
    return [
        repository
        for repository in usable
        if repository.id not in source.left_out
        and (repository.id in already or repository.pushed_at is None or repository.pushed_at >= earliest)
    ]


def follows(team: TeamConfig, service: str, source: str, repository_id: str) -> bool:
    """Whether the team's selection for that source covers the repository, not looking at its state on the service."""
    return any(
        selection.service == service
        and selection.source == source
        and (
            repository_id in selection.chosen
            if selection.follow == "chosen"
            else repository_id not in selection.left_out
        )
        for selection in team.sources
    )
