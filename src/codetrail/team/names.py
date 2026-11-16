"""Names of the targets a team creates (design section 24.4)."""

from __future__ import annotations

import re
from itertools import chain, count

MAX_TARGET_NAME_LENGTH = 63  # TARGET_NAME in codetrail.config
ID_SUFFIX_CHARACTERS = 6


def team_target_name(team: str, repository_name: str, taken: set[str], service_id: str = "") -> str:
    """`<team>-<repository>`, lower-cased, with every run of other characters one hyphen, cut to a target name's
    length. On a clash it ends with `-` and the first six lower-case letters and digits of the service's id, and
    while that is taken too (ids often start alike), with `-2`, `-3` and so on after them."""
    full_name = f"{team}-{re.sub(r'[^a-z0-9-]+', '-', repository_name.lower())}"
    name = full_name[:MAX_TARGET_NAME_LENGTH].strip("-")
    if name not in taken:
        return name
    suffix = "".join(re.findall(r"[a-z0-9]", service_id.lower()))[:ID_SUFFIX_CHARACTERS]
    endings = chain([suffix] if suffix else [], (f"{suffix}-{number}".lstrip("-") for number in count(2)))
    for ending in endings:
        candidate = full_name[: MAX_TARGET_NAME_LENGTH - len(ending) - 1].strip("-") + f"-{ending}"
        if candidate not in taken:
            return candidate
    raise AssertionError("The endings never run out.")
