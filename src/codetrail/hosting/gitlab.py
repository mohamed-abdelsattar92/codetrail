"""GitLab through the reader's `glab` (design section 24.3)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

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
from codetrail.hosting.names import Service, validate_source
from codetrail.hosting.run import run_read

INSTALL = "Install glab from https://gitlab.com/gitlab-org/cli"


class GitLab:
    service: Service = "gitlab"

    def __init__(self, settings: HostingSettings) -> None:
        self._settings = settings

    def _read(self, arguments: list[str]) -> str:
        return run_read("gitlab", self._settings.glab, arguments, self._settings)

    def status(self) -> HostingStatus:
        """Signed in when glab exits 0 with a host; the account's name isn't documented for the JSON form, so none."""
        if not is_installed(self._settings.glab):
            return HostingStatus("gitlab", False, False, None, INSTALL)
        try:
            document = json_documents(self._read(["auth", "status", "--output", "json"]))[0]
            signed_in = bool(document.get("hosts")) if isinstance(document, dict) else bool(document)
        except HostingError, IndexError:
            signed_in = False
        return HostingStatus("gitlab", True, signed_in, None, None if signed_in else "glab auth login")

    def sources(self, organization: str | None = None) -> list[Source]:
        output = self._read(["api", "--method", "GET", "groups?min_access_level=10", "--paginate"])
        sources: list[Source] = []
        for record in json_records(output):
            try:
                sources.append(Source("gitlab", validate_source("gitlab", record["full_path"]), None))
            except CodetrailError, KeyError, TypeError:
                continue
        return sources

    def repositories(self, source: str) -> list[HostedRepository]:
        group = quote(validate_source("gitlab", source), safe="")
        output = self._read(
            ["api", "--method", "GET", f"groups/{group}/projects?include_subgroups=true&archived=false", "--paginate"]
        )

        def build(record: dict[str, Any]) -> HostedRepository:
            default_branch = None if record.get("empty_repo") else record.get("default_branch") or None
            return checked_repository(
                "gitlab",
                source,
                {
                    "id": record["id"],
                    "name": record["path"],  # the path is the name in the URL; the display name may hold anything
                    "default_branch": default_branch,
                    "archived": record["archived"],
                    "disabled": record.get("repository_access_level") == "disabled",
                    "pushed_at": record.get("last_activity_at"),
                    "size_kb": None,
                    "web_url": record["web_url"],
                },
                record["http_url_to_repo"],
                record["ssh_url_to_repo"],
            )

        return build_repositories("gitlab", json_records(output), self._settings.max_repositories_per_source, build)

    def branches(self, repository: HostedRepository) -> list[str]:
        if not repository.id.isdigit():
            raise CodetrailError("A GitLab project id is a number.")
        output = self._read(["api", "--method", "GET", f"projects/{repository.id}/repository/branches", "--paginate"])
        return branch_names([record.get("name") for record in json_records(output)])
