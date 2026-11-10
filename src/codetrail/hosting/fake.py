"""A hosting service from plain data, for tests; it records each call."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from codetrail.hosting import HostedRepository, HostingError, HostingStatus, Source
from codetrail.hosting.names import Service


class FakeHosting:
    """`statuses` are answered in order, the last one repeating; `repositories` is by source name and `branches` by
    repository id."""

    def __init__(
        self,
        service: Service,
        statuses: Sequence[HostingStatus],
        sources: Sequence[Source],
        repositories: Mapping[str, Sequence[HostedRepository]],
        branches: Mapping[str, Sequence[str]],
    ) -> None:
        self.service = service
        self._statuses = list(statuses)
        self._sources = list(sources)
        self._repositories = {name: list(found) for name, found in repositories.items()}
        self._branches = {repository_id: list(found) for repository_id, found in branches.items()}
        self.calls: list[tuple[str, ...]] = []

    def status(self) -> HostingStatus:
        self.calls.append(("status",))
        return self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]

    def sources(self, organization: str | None = None) -> list[Source]:
        self.calls.append(("sources", organization or ""))
        return list(self._sources)

    def repositories(self, source: str) -> list[HostedRepository]:
        self.calls.append(("repositories", source))
        if source not in self._repositories:
            raise HostingError(f"The fake has no source {source!r}.")
        return list(self._repositories[source])

    def branches(self, repository: HostedRepository) -> list[str]:
        self.calls.append(("branches", repository.id))
        return list(self._branches.get(repository.id, []))
