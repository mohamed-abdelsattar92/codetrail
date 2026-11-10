"""The hosting services and the clone URLs Codetrail accepts (design section 24.3)."""

from __future__ import annotations

import re
import unicodedata
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

GITHUB_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
GITHUB_REPOSITORY = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]{0,99}")
AZURE_ORGANIZATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,49}")
AZURE_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|;#$*{},+=\[\]]")
GITLAB_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}")
GITLAB_MAX_SEGMENTS = 20


def _refuse(service: Service, kind: str, name: str) -> CodetrailError:
    return CodetrailError(f"{name!r} isn't a valid {kind} name for {service}.")


def _azure_name(name: str) -> bool:
    return (
        1 <= len(name) <= 64
        and not AZURE_FORBIDDEN.search(name)
        and name[0] not in "._-"
        and not any(unicodedata.category(character) in ("Cc", "Cf") for character in name)
        and not name.endswith(".")
        and name == name.strip()
    )


def _gitlab_path(path: str) -> bool:
    segments = path.split("/")
    return len(segments) <= GITLAB_MAX_SEGMENTS and all(
        GITLAB_SEGMENT.fullmatch(segment) and ".." not in segment for segment in segments
    )


def validate_source(service: Service, name: str) -> str:
    """Returns a source's name (a GitHub owner, an Azure DevOps `<organization>/<project>`, a GitLab group path), or
    raises. It is passed to the service's program as its own argument."""
    if service == "github":
        valid = bool(GITHUB_OWNER.fullmatch(name))
    elif service == "azure_devops":
        organization, _, project = name.partition("/")
        valid = bool(AZURE_ORGANIZATION.fullmatch(organization)) and _azure_name(project)
    elif service == "gitlab":
        valid = _gitlab_path(name)
    else:
        raise CodetrailError(f"{service!r} isn't a hosting service.")
    if not valid:
        raise _refuse(service, "source", name)
    return name


def validate_repository_name(service: Service, name: str) -> str:
    """Returns a repository's name, or raises."""
    if service == "github":
        valid = bool(GITHUB_REPOSITORY.fullmatch(name)) and name not in (".", "..")
    elif service == "azure_devops":
        valid = _azure_name(name)
    elif service == "gitlab":
        valid = _gitlab_path(name) and "/" not in name
    else:
        raise CodetrailError(f"{service!r} isn't a hosting service.")
    if not valid:
        raise _refuse(service, "repository", name)
    return name


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
        scp_prefix = f"git@{hosts.ssh}:"
        if url.startswith(scp_prefix) and len(url) > len(scp_prefix) and not url[len(scp_prefix) :].startswith("-"):
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


# A service's own id (a node id, a UUID or a number) is passed to its program as an argument, so it can't lead with `-`.
SAFE_ID_CHARACTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-=")


def require_safe_id(identifier: str) -> str:
    """The id, or an error when it could be read as a flag or holds anything but letters, digits, `_`, `-` and `=`."""
    if not identifier or identifier[0] == "-" or not set(identifier) <= SAFE_ID_CHARACTERS:
        raise CodetrailError("A repository id isn't acceptable.")
    return identifier
