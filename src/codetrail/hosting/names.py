"""The hosting services and the clone URLs Codetrail accepts (design section 24.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args
from urllib.parse import urlsplit

from codetrail.errors import CodetrailError

Service = Literal["github", "azure_devops", "gitlab"]


@dataclass(frozen=True)
class Hosts:
    https: str
    ssh: str


# Only code changes these; no setting can widen them.
HOSTS: dict[Service, Hosts] = {
    "github": Hosts(https="github.com", ssh="github.com"),
    "azure_devops": Hosts(https="dev.azure.com", ssh="ssh.dev.azure.com"),
    "gitlab": Hosts(https="gitlab.com", ssh="gitlab.com"),
}

_UNSAFE_CHARACTERS = re.compile(r"[\s\x00-\x1f\x7f]")


def check_clone_url(service: Service, url: str) -> str:
    """Returns the clone URL, or raises unless it is https on the service's https host (no user information, no
    port) or SSH as `git@<ssh host>:path` or `ssh://git@<ssh host>/path`.

    Azure DevOps writes its https URLs as `https://<organization>@dev.azure.com/<organization>/...`; that user name
    is dropped, so git is never given a user.
    """
    if service not in get_args(Service):
        raise CodetrailError(f"{service!r} isn't a hosting service.")
    hosts = HOSTS[service]
    if not _UNSAFE_CHARACTERS.search(url):
        if url.startswith(f"git@{hosts.ssh}:") and len(url) > len(f"git@{hosts.ssh}:"):
            return url
        parts = urlsplit(url)
        if parts.scheme == "ssh" and parts.netloc == f"git@{hosts.ssh}" and len(parts.path) > 1:
            return url
        if parts.scheme == "https" and len(parts.path) > 1 and not parts.fragment:
            if parts.netloc == hosts.https:
                return url
            organization = parts.path.split("/")[1]
            if service == "azure_devops" and parts.netloc == f"{organization}@{hosts.https}":
                return f"https://{hosts.https}{parts.path}" + (f"?{parts.query}" if parts.query else "")
    raise CodetrailError(
        f"{url!r} isn't a clone URL for {service}: use https://{hosts.https}/... or git@{hosts.ssh}:..., "
        "with no user, password or port."
    )
