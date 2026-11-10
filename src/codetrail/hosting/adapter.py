"""What the three hosting adapters share: finding the program, reading its JSON and building a checked repository."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from codetrail.assistant import AssistantError
from codetrail.assistant.environment import resolve_program
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, HostingError
from codetrail.hosting.names import HOSTS, Service, check_clone_url, validate_repository_name
from codetrail.hosting.run import hosting_environment

logger = logging.getLogger(__name__)

# A service's own id (a node id, a UUID or a number) is passed to its program as an argument, so it can't lead with `-`.
MAX_LOGGED_ID = 64
SAFE_ID_CHARACTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-=")


def require_safe_id(identifier: str) -> str:
    """The id, or an error when it could be read as a flag or holds anything but letters, digits, `_`, `-` and `=`."""
    if not identifier or identifier[0] == "-" or not set(identifier) <= SAFE_ID_CHARACTERS:
        raise CodetrailError("A repository id isn't acceptable.")
    return identifier


def is_installed(program: str) -> bool:
    try:
        resolve_program(program, hosting_environment(os.environ)["PATH"])
    except AssistantError:
        return False
    return True


def json_documents(output: str) -> list[Any]:
    """Every JSON document in `output`: one, or several one after another (as `--paginate` may print pages)."""
    decoder = json.JSONDecoder()
    text = output.strip()
    documents: list[Any] = []
    position = 0
    try:
        while position < len(text):
            document, position = decoder.raw_decode(text, position)
            documents.append(document)
            while position < len(text) and text[position].isspace():
                position += 1
    except json.JSONDecodeError as error:
        raise HostingError("A hosting program returned something that isn't JSON.") from error
    return documents


def json_records(output: str, list_key: str | None = None) -> list[dict[str, Any]]:
    """The objects in the program's JSON arrays, or in `list_key` of an object when the program wraps its list."""
    records: list[dict[str, Any]] = []
    for document in json_documents(output):
        items = document.get(list_key, []) if isinstance(document, dict) and list_key else document
        if isinstance(items, list):
            records.extend(item for item in items if isinstance(item, dict))
    return records


def branch_names(names: list[Any]) -> list[str]:
    """Branch names that are text, don't lead with `-` and hold no control characters."""
    return [
        name
        for name in names
        if isinstance(name, str) and name and not name.startswith("-") and name.isprintable() and name == name.strip()
    ]


def _is_https_on(service: Service, url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc == HOSTS[service].https and url.isprintable() and " " not in url


def parse_time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def checked_repository(
    service: Service,
    source: str,
    fields: Mapping[str, Any],
    https_url: str,
    ssh_url: str,
) -> HostedRepository:
    """The repository, or an error when its id, name or URLs aren't acceptable."""
    identifier = require_safe_id(str(fields["id"]))
    web_url = fields["web_url"]
    default_branch = fields["default_branch"]
    if not isinstance(web_url, str) or not _is_https_on(service, web_url):
        raise CodetrailError("A repository's web URL isn't https on the service's host.")
    if default_branch is not None and not branch_names([default_branch]):
        raise CodetrailError("A repository's default branch isn't acceptable.")
    return HostedRepository(
        service=service,
        source=source,
        id=identifier,
        name=validate_repository_name(service, fields["name"]),
        default_branch=default_branch,
        archived=bool(fields["archived"]),
        disabled=bool(fields["disabled"]),
        pushed_at=parse_time(fields["pushed_at"]),
        size_kb=fields["size_kb"],
        https_url=check_clone_url(service, https_url),
        ssh_url=check_clone_url(service, ssh_url),
        web_url=web_url,
    )


def build_repositories(
    service: Service,
    records: list[dict[str, Any]],
    limit: int,
    build: Callable[[dict[str, Any]], HostedRepository],
) -> list[HostedRepository]:
    """Reads at most `limit` records; one that fails a check is left out and logged by its id, never its name."""
    repositories: list[HostedRepository] = []
    if len(records) > limit:
        logger.warning("A %s source has more repositories than the limit of %d; the rest are not read.", service, limit)
    for record in records[:limit]:
        try:
            repositories.append(build(record))
        except CodetrailError, KeyError, TypeError, ValueError:
            identifier = str(record.get("id"))[:MAX_LOGGED_ID]
            logger.warning("Left out a %s repository that failed its checks (id %r).", service, identifier)
    return repositories
