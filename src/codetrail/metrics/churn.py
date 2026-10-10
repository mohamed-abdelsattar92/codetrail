"""Where change happens: hot spots by file, folder and guide area, quiet code and commit size (design section 19.1).

Only allowed files are ever named: a commit's other files count toward its size, never toward a hot spot.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from codetrail.generate.outline import in_scope
from codetrail.guide import Page


@dataclass(frozen=True)
class HotSpot:
    name: str  # a file, or a top-level folder ("." for the repository's root)
    count: int  # commits that changed it, or for quiet code, its quiet files


@dataclass(frozen=True)
class AreaSpot:
    page: Page
    commits: int
    documented: int
    inferred: int


@dataclass(frozen=True)
class Churn:
    commits: int  # read: the latest non-merge commits, up to the window
    files: list[HotSpot]  # the most changed first
    folders: list[HotSpot]
    areas: list[AreaSpot]
    quiet: list[HotSpot]  # code files no recent commit changed, counted by folder
    quiet_files: int
    median_files: float | None  # files a commit changes
    p90_files: float | None


def _folder(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "."


def _most_first(counts: Counter[str]) -> list[HotSpot]:
    return [HotSpot(name, count) for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def measure_churn(
    changes: Sequence[Sequence[str]],
    allowed: Collection[str],
    code: Collection[str],
    recently_changed: Collection[str],
    areas: Sequence[tuple[Page, list[str], int, int]],
) -> Churn:
    """`changes` holds each commit's paths; `areas` each area page with its scope paths and rationale counts."""
    files: Counter[str] = Counter()
    folders: Counter[str] = Counter()
    by_area: Counter[str] = Counter()
    for paths in changes:
        visible = {path for path in paths if path in allowed}
        files.update(visible)
        folders.update({_folder(path) for path in visible})
        for page, scope_paths, _documented, _inferred in areas:
            if any(in_scope(path, scope_paths) for path in visible):
                by_area[page.id] += 1
    spots = [AreaSpot(page, by_area[page.id], documented, inferred) for page, _, documented, inferred in areas]
    spots.sort(key=lambda spot: (-spot.commits, spot.page.title))
    quiet = [path for path in code if path not in recently_changed]
    sizes = [len(paths) for paths in changes]
    median = statistics.median(sizes) if sizes else None
    p90 = statistics.quantiles(sizes, n=10)[8] if len(sizes) > 1 else median
    return Churn(len(changes), _most_first(files), _most_first(folders), spots,
                 _most_first(Counter(_folder(path) for path in quiet)), len(quiet), median, p90)  # fmt: skip
