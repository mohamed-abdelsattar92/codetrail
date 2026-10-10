"""Releases: the target's tags that point into the branch's history (design section 19.1)."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from codetrail.repo.history import Tag

DAY_SECONDS = 86_400


@dataclass(frozen=True)
class Releases:
    tags: list[Tag] = field(default_factory=list)  # newest first
    since_latest: int | None = None  # commits after the latest release
    median_days: int | None = None  # between consecutive releases


def measure_releases(tags: Sequence[Tag], since_latest: int | None) -> Releases:
    newest_first = sorted(tags, key=lambda tag: (tag.date, tag.name), reverse=True)
    gaps = [newer.date - older.date for newer, older in pairwise(newest_first)]
    median = round(statistics.median(gaps) / DAY_SECONDS) if gaps else None
    return Releases(newest_first, since_latest, median)
