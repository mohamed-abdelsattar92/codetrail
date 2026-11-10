"""Azure DevOps through the reader's `az` with its `azure-devops` extension (design section 24.3)."""

from __future__ import annotations

from typing import Any

from codetrail.config import HostingSettings
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, HostingError, HostingStatus, Source
from codetrail.hosting.adapter import (
    branch_names,
    build_repositories,
    checked_repository,
    is_installed,
    json_documents,
    json_records,
)
from codetrail.hosting.names import AZURE_ORGANIZATION, Service, require_safe_id, validate_source
from codetrail.hosting.run import run_read

BRANCH_PREFIX = "refs/heads/"
INSTALL = (
    "Install the Azure CLI from https://learn.microsoft.com/cli/azure/install-azure-cli, "
    "then run: az extension add --name azure-devops"
)


def organization_url(organization: str) -> str:
    if not AZURE_ORGANIZATION.fullmatch(organization):
        raise CodetrailError(f"{organization!r} isn't a valid organization name for azure_devops.")
    return f"https://dev.azure.com/{organization}"


def without_prefix(name: object) -> str | None:
    return name.removeprefix(BRANCH_PREFIX) if isinstance(name, str) and name else None


class AzureDevOps:
    service: Service = "azure_devops"

    def __init__(self, settings: HostingSettings) -> None:
        self._settings = settings

    def _read(self, arguments: list[str]) -> str:
        return run_read("azure_devops", self._settings.az, arguments, self._settings)

    def status(self) -> HostingStatus:
        if not is_installed(self._settings.az):
            return HostingStatus("azure_devops", False, False, None, INSTALL)
        try:
            user = json_documents(self._read(["account", "show", "-o", "json"]))[0]["user"]
            account = user.get("name")
        except HostingError, IndexError, KeyError, TypeError, AttributeError:
            return HostingStatus("azure_devops", True, False, None, "az login")
        return HostingStatus("azure_devops", True, True, account if isinstance(account, str) else None, None)

    def sources(self, organization: str | None = None) -> list[Source]:
        """Organizations can't be listed, so the reader names one and its projects are the sources."""
        if organization is None:
            return []
        url = organization_url(organization)
        output = self._read(["devops", "project", "list", "--organization", url, "-o", "json"])
        names = (f"{organization}/{record['name']}" for record in json_records(output, "value"))
        sources: list[Source] = []
        for name in names:
            try:
                sources.append(Source("azure_devops", validate_source("azure_devops", name), None))
            except CodetrailError:
                continue
        return sources

    def repositories(self, source: str) -> list[HostedRepository]:
        organization, _, project = validate_source("azure_devops", source).partition("/")
        output = self._read(
            ["repos", "list", "--organization", organization_url(organization), "--project", project, "-o", "json"]
        )

        def build(record: dict[str, Any]) -> HostedRepository:
            size = record.get("size")
            return checked_repository(
                "azure_devops",
                source,
                {
                    "id": record["id"],
                    "name": record["name"],
                    "default_branch": without_prefix(record.get("defaultBranch")),
                    "archived": False,
                    "disabled": record.get("isDisabled", False),
                    "pushed_at": None,
                    "size_kb": size // 1024 if isinstance(size, int) else None,
                    "web_url": record["webUrl"],
                },
                record["remoteUrl"],
                record["sshUrl"],
            )

        return build_repositories(
            "azure_devops", json_records(output), self._settings.max_repositories_per_source, build
        )

    def branches(self, repository: HostedRepository) -> list[str]:
        organization, _, project = validate_source("azure_devops", repository.source).partition("/")
        output = self._read(
            [
                "repos",
                "ref",
                "list",
                "--organization",
                organization_url(organization),
                "--project",
                project,
                "--repository",
                require_safe_id(repository.id),
                "--filter",
                "heads/",
                "-o",
                "json",
            ]
        )
        return branch_names([without_prefix(record.get("name")) for record in json_records(output)])
