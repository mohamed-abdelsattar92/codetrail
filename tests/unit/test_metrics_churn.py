"""Where change happens: hot spots, the heat map's tiles, the area-by-month grid and commit size (sections 19.1, 20)."""

from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime

from codetrail.guide import Page
from codetrail.metrics.churn import AreaSpot, Churn, HeatTile, HotSpot, measure_churn

TODAY = date(2026, 10, 10)
ALLOWED = {"app/db.py", "app/main.py", "web/src/x.ts", "README.md", "tools/old.py"}
CODE = {"app/db.py": 100, "app/main.py": 50, "web/src/x.ts": 80, "tools/old.py": 30}


def at(year: int, month: int, day: int = 15) -> int:
    return int(datetime(year, month, day, 12, tzinfo=UTC).timestamp())


CHANGES: list[tuple[int, list[str]]] = [  # newest first
    (at(2026, 10), ["app/db.py", ".env"]),
    (at(2026, 9), ["app/db.py", "web/src/x.ts"]),
    (at(2026, 9), ["app/main.py", "app/db.py", "README.md"]),
    (at(2026, 8), [".env"]),
    (at(2026, 7), []),
]


def area(page_id: str) -> Page:
    return Page(page_id, {"kind": "area", "title": page_id})


def churn(
    areas: Sequence[tuple[Page, list[str], int, int]] = (),
    depth: int = 2,
    cap: int = 60,
    window: int = 500,
    recent: Collection[str] = frozenset(),
) -> Churn:
    return measure_churn(CHANGES, ALLOWED, CODE, recent, areas, TODAY, depth, cap, window)


def test_files_and_folders_are_counted_once_per_commit_and_only_allowed_ones_named() -> None:
    found = churn(depth=1)
    assert found.commits == 5
    assert found.files == [HotSpot("app/db.py", 3), HotSpot("README.md", 1), HotSpot("app/main.py", 1),
                           HotSpot("web/src/x.ts", 1)]  # fmt: skip
    assert found.folders == [HotSpot("app", 3), HotSpot(".", 1), HotSpot("web", 1)]
    assert all(".env" not in spot.name for spot in found.files + found.folders)


def test_folders_go_as_deep_as_configured() -> None:
    assert [spot.name for spot in churn(depth=2).folders] == ["app", ".", "web/src"]


def test_areas_count_commits_and_lines_in_their_scope_with_their_rationale() -> None:
    app, web, empty = area("areas/app"), area("areas/web"), area("areas/empty")
    found = churn(areas=[(app, ["app"], 1, 4), (web, ["web/"], 2, 0), (empty, ["docs"], 0, 0)])
    assert found.areas == [AreaSpot(app, 3, 1, 4, 150), AreaSpot(web, 1, 2, 0, 80), AreaSpot(empty, 0, 0, 0, 0)]


def test_heat_tiles_are_the_largest_by_lines_with_the_rest_counted() -> None:
    found = churn(depth=2, cap=2)
    assert found.file_tiles == [HeatTile("db.py", "app/db.py", 100, 3), HeatTile("x.ts", "web/src/x.ts", 80, 1)]
    assert found.folder_tiles == [HeatTile("app", "app", 150, 3), HeatTile("web/src", "web/src", 80, 1)]
    assert found.left_out == {"areas": 0, "folders": 1, "files": 2}


def test_the_grid_counts_each_area_per_month() -> None:
    app = area("areas/app")
    found = churn(areas=[(app, ["app"], 0, 0)])
    assert found.months[-1] == "2026-10" and len(found.months) == 12
    assert found.area_months == [("areas/app", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 1])]


def test_months_before_the_oldest_commit_read_are_unread_when_the_window_was_cut() -> None:
    app = area("areas/app")
    found = churn(areas=[(app, ["app"], 0, 0)], window=5)
    assert found.area_months[0][1][:8] == [None] * 8  # the oldest commit read is from July 2026
    assert found.area_months[0][1][8:] == [0, 0, 2, 1]


def test_quiet_code_is_code_no_recent_commit_changed_by_folder() -> None:
    found = churn(depth=1, recent={"app/db.py", "web/src/x.ts", "README.md"})
    assert found.quiet == [HotSpot("app", 1), HotSpot("tools", 1)]
    assert found.quiet_files == 2


def test_commit_size_and_its_histogram_count_every_file_excluded_ones_too() -> None:
    found = churn()
    assert (found.median_files, found.p90_files) == (2, 2.6)  # sizes 2, 2, 3, 1, 0
    assert found.size_histogram == [1, 2, 1, 0, 0, 0, 0]  # 1, 2, 3, 4-5, 6-10, 11-20, 21+


def test_the_90th_percentile_never_passes_the_largest_commit() -> None:
    changes = [(at(2026, 10), ["a"])] * 3 + [(at(2026, 10), [str(number) for number in range(20)])]
    found = measure_churn(changes, set(), {}, set(), [], TODAY, 2, 60, 500)
    assert found.p90_files is not None and found.p90_files <= 20
    assert found.size_histogram == [3, 0, 0, 0, 0, 1, 0]


def test_no_commits_give_nothing() -> None:
    none = measure_churn([], ALLOWED, CODE, set(), [], TODAY, 2, 60, 500)
    assert (none.commits, none.files, none.median_files, none.p90_files) == (0, [], None, None)
    assert none.quiet_files == len(CODE)
