"""The adapter for each hosting service (design section 24.3)."""

from __future__ import annotations

from codetrail.config import GlobalConfig
from codetrail.hosting import HostingService
from codetrail.hosting.azure_devops import AzureDevOps
from codetrail.hosting.github import GitHub
from codetrail.hosting.gitlab import GitLab
from codetrail.hosting.names import Service


def services_for(settings: GlobalConfig) -> dict[Service, HostingService]:
    return {
        "github": GitHub(settings.hosting),
        "azure_devops": AzureDevOps(settings.hosting),
        "gitlab": GitLab(settings.hosting),
    }
