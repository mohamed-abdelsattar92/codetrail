"""Hosting services (GitHub, Azure DevOps, GitLab) as Codetrail reads them (design section 24.3).

One interface, shaped by what Codetrail needs; an adapter per service and a fake implement it, and nothing outside
an adapter knows which service it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from codetrail.errors import CodetrailError
from codetrail.hosting.names import Service, check_clone_url


class HostingError(CodetrailError):
    """A hosting program is missing, refused, or returned something Codetrail can't use."""


@dataclass(frozen=True)
class HostingStatus:
    service: Service
    installed: bool
    signed_in: bool
    account: str | None
    fix: str | None  # the command the reader runs, when the program is missing or signed out


@dataclass(frozen=True)
class Source:
    """What a sign-in can see: a GitHub owner, an Azure DevOps `<organization>/<project>`, a GitLab group."""

    service: Service
    name: str
    repository_count: int | None  # None while unknown (no service reports it before `repositories()`)


@dataclass(frozen=True)
class HostedRepository:
    service: Service
    source: str
    id: str
    name: str
    default_branch: str | None
    archived: bool
    disabled: bool
    pushed_at: datetime | None
    size_kb: int | None
    https_url: str
    ssh_url: str
    web_url: str

    def clone_url(self, clone_over: Literal["https", "ssh"]) -> str:
        """The URL to clone from, checked against the service's hosts."""
        return check_clone_url(self.service, self.https_url if clone_over == "https" else self.ssh_url)


class HostingService(Protocol):
    service: Service

    def status(self) -> HostingStatus: ...

    def sources(self, organization: str | None = None) -> list[Source]:
        """`organization` is used by Azure DevOps only (`az` can't list organizations); the others ignore it."""
        ...

    def repositories(self, source: str) -> list[HostedRepository]: ...

    def branches(self, repository: HostedRepository) -> list[str]: ...
