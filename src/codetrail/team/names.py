"""Names of the targets a team creates (design section 24.4)."""

from __future__ import annotations

import re

from codetrail.errors import CodetrailError

MAX_TARGET_NAME_LENGTH = 63  # TARGET_NAME in codetrail.config
ID_SUFFIX_CHARACTERS = 6


def team_target_name(team: str, repository_name: str, taken: set[str], service_id: str = "") -> str:
    """`<team>-<repository>`, lower-cased, with every run of other characters one hyphen, cut to a target name's
    length. On a clash it ends with `-` and the first six lower-case letters and digits of the service's id."""
    full_name = f"{team}-{re.sub(r'[^a-z0-9-]+', '-', repository_name.lower())}"
    name = full_name[:MAX_TARGET_NAME_LENGTH].strip("-")
    if name not in taken:
        return name
    suffix = "".join(re.findall(r"[a-z0-9]", service_id.lower()))[:ID_SUFFIX_CHARACTERS]
    if not suffix:
        raise CodetrailError(f"A target named {name!r} exists and the repository has no id to tell it apart.")
    return full_name[: MAX_TARGET_NAME_LENGTH - len(suffix) - 1].strip("-") + f"-{suffix}"
