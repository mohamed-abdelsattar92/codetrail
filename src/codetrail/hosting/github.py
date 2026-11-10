"""GitHub through the reader's `gh` (design section 24.3)."""

from __future__ import annotations

import logging
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
from codetrail.hosting.names import Service, validate_repository_name, validate_source
from codetrail.hosting.run import run_read

logger = logging.getLogger(__name__)
REPOSITORY_FIELDS = "id,name,defaultBranchRef,isArchived,isEmpty,isFork,isTemplate,pushedAt,diskUsage,url,sshUrl"
INSTALL = "Install the GitHub CLI from https://cli.github.com"


class GitHub:
    service: Service = "github"

    def __init__(self, settings: HostingSettings) -> None:
        self._settings = settings

    def _read(self, arguments: list[str]) -> str:
        return run_read("github", self._settings.gh, arguments, self._settings)

    def status(self) -> HostingStatus:
        if not is_installed(self._settings.gh):
            return HostingStatus("github", False, False, None, INSTALL)
        try:
            hosts = json_documents(self._read(["auth", "status", "--json", "hosts"]))[0]["hosts"]
            account = next(
                entry["login"]
                for entry in hosts["github.com"]
                if entry["state"] == "success" and entry["active"] is True
            )
        except HostingError, IndexError, KeyError, StopIteration, TypeError:
            return HostingStatus("github", True, False, None, "gh auth login")
        return HostingStatus("github", True, True, account if isinstance(account, str) else None, None)

    def sources(self, organization: str | None = None) -> list[Source]:
        names: list[str] = []
        account = self.status().account
        if account is not None:
            names.append(account)
        organizations = json_records(self._read(["api", "--method", "GET", "user/orgs", "--paginate"]))
        names.extend(record["login"] for record in organizations if isinstance(record.get("login"), str))
        sources: list[Source] = []
        for name in dict.fromkeys(names):
            try:
                sources.append(Source("github", validate_source("github", name), None))
            except CodetrailError:
                logger.warning("Left out a github source whose name failed its checks.")
        return sources

    def repositories(self, source: str) -> list[HostedRepository]:
        validate_source("github", source)
        limit = self._settings.max_repositories_per_source
        output = self._read(["repo", "list", source, "--json", REPOSITORY_FIELDS, "--limit", str(limit)])

        def build(record: dict[str, Any]) -> HostedRepository:
            branch = record.get("defaultBranchRef") or {}
            url = record["url"]
            return checked_repository(
                "github",
                source,
                {
                    "id": record["id"],
                    "name": record["name"],
                    "default_branch": None if record.get("isEmpty") else branch.get("name"),
                    "archived": record["isArchived"],
                    "disabled": False,
                    "pushed_at": record.get("pushedAt"),
                    "size_kb": record.get("diskUsage"),
                    "web_url": url,
                },
                f"{url}.git",
                record["sshUrl"],
            )

        return build_repositories("github", json_records(output), limit, build)

    def branches(self, repository: HostedRepository) -> list[str]:
        owner = validate_source("github", repository.source)
        name = validate_repository_name("github", repository.name)
        output = self._read(
            ["api", "--method", "GET", f"repos/{owner}/{name}/branches", "--paginate", "--jq", ".[].name"]
        )
        return branch_names(output.splitlines())
