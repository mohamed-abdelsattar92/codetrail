"""Where change happens: hot spots, the heat map's tiles, the area-by-month grid, quiet code and commit size.

Design sections 19.1 and 20.1. Only allowed files are ever named: a commit's other files count toward its size, never
toward a hot spot or a tile.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from codetrail.generate.outline import in_scope
from codetrail.guide import Page

MONTHS_SHOWN = 12
SIZE_BUCKETS = (1, 2, 3, 5, 10, 20)  # upper bounds of 1, 2, 3, 4-5, 6-10, 11-20; anything larger is 21+


@dataclass(frozen=True)
class HotSpot:
    name: str  # a file, or a folder ("." for the repository's root)
    count: int  # commits that changed it, or for quiet code, its quiet files


@dataclass(frozen=True)
class AreaSpot:
    page: Page
    commits: int
    documented: int
    inferred: int
    lines: int = 0  # lines of code in the area's scope


@dataclass(frozen=True)
class HeatTile:
    name: str
    path: str
    lines: int
    commits: int


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
    folder_tiles: list[HeatTile] = field(default_factory=list)  # the largest by lines, at most the cap
    file_tiles: list[HeatTile] = field(default_factory=list)
    left_out: dict[str, int] = field(default_factory=dict)  # tiles past the cap, per level
    months: list[str] = field(default_factory=list)  # "YYYY-MM", the last twelve, oldest first
    area_months: list[tuple[str, list[int | None]]] = field(default_factory=list)  # None: a month not read
    size_histogram: list[int] = field(default_factory=list)  # commits per bucket of files changed


def folder_of(path: str, depth: int) -> str:
    """The file's folder, at most `depth` levels deep; "." for a file at the root."""
    return "/".join(path.split("/")[:-1][:depth]) or "."


def _most_first(counts: Counter[str]) -> list[HotSpot]:
    return [HotSpot(name, count) for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _month(time: int) -> str:
    return datetime.fromtimestamp(time, UTC).strftime("%Y-%m")


def _last_months(today: date) -> list[str]:
    year, month, months = today.year, today.month, []
    for _ in range(MONTHS_SHOWN):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return months[::-1]


def _bucket(size: int) -> int:
    return next((index for index, bound in enumerate(SIZE_BUCKETS) if size <= bound), len(SIZE_BUCKETS))


def _capped(tiles: list[HeatTile], cap: int) -> list[HeatTile]:
    return sorted(tiles, key=lambda tile: (-tile.lines, tile.path))[:cap]


def measure_churn(
    changes: Sequence[tuple[int, Sequence[str]]],
    allowed: Collection[str],
    code_lines: Mapping[str, int],
    recently_changed: Collection[str],
    areas: Sequence[tuple[Page, list[str], int, int]],
    today: date,
    folder_depth: int,
    tile_cap: int,
    window: int,
) -> Churn:
    """`changes` holds each commit's time and paths, newest first; `areas` each area page with its scope and rationale.

    `window` is how many commits were asked for: when that many came back, the history goes further back than was
    read, and the grid's months before the oldest commit read are unread rather than quiet.
    """
    files: Counter[str] = Counter()
    folders: Counter[str] = Counter()
    by_area: Counter[str] = Counter()
    by_area_month: Counter[tuple[str, str]] = Counter()
    for time, paths in changes:
        visible = {path for path in paths if path in allowed}
        files.update(visible)
        folders.update({folder_of(path, folder_depth) for path in visible})
        for page, scope_paths, _documented, _inferred in areas:
            if any(in_scope(path, scope_paths) for path in visible):
                by_area[page.id] += 1
                by_area_month[page.id, _month(time)] += 1
    spots = [
        AreaSpot(page, by_area[page.id], documented, inferred,
                 sum(lines for path, lines in code_lines.items() if in_scope(path, scope_paths)))
        for page, scope_paths, documented, inferred in areas
    ]  # fmt: skip
    spots.sort(key=lambda spot: (-spot.commits, spot.page.title))
    folder_lines: Counter[str] = Counter()
    for path, lines in code_lines.items():
        folder_lines[folder_of(path, folder_depth)] += lines
    folder_tiles = [HeatTile(name, name, lines, folders[name]) for name, lines in folder_lines.items()]
    file_tiles = [HeatTile(path.rsplit("/", 1)[-1], path, lines, files[path]) for path, lines in code_lines.items()]
    months = _last_months(today)
    oldest = _month(changes[-1][0]) if changes and len(changes) >= window else None
    area_months = [
        (page.id, [None if oldest and month < oldest else by_area_month[page.id, month] for month in months])
        for page, _scope, _documented, _inferred in areas
    ]
    quiet = [path for path in code_lines if path not in recently_changed]
    sizes = [len(paths) for _time, paths in changes]
    histogram = [0] * (len(SIZE_BUCKETS) + 1)
    for size in sizes:
        if size:
            histogram[_bucket(size)] += 1
    median = statistics.median(sizes) if sizes else None
    # Inclusive: the default method extrapolates past the largest commit when there are few.
    p90 = statistics.quantiles(sizes, n=10, method="inclusive")[8] if len(sizes) > 1 else median
    left_out = {"areas": max(0, len(spots) - tile_cap), "folders": max(0, len(folder_tiles) - tile_cap),
                "files": max(0, len(file_tiles) - tile_cap)}  # fmt: skip
    return Churn(
        len(changes), _most_first(files), _most_first(folders), spots,
        _most_first(Counter(folder_of(path, folder_depth) for path in quiet)), len(quiet), median, p90,
        _capped(folder_tiles, tile_cap), _capped(file_tiles, tile_cap), left_out, months, area_months, histogram,
    )  # fmt: skip
