"""Where change happens: hot spots, quiet code and commit size (design section 19.1)."""

from codetrail.guide import Page
from codetrail.metrics.churn import AreaSpot, HotSpot, measure_churn

ALLOWED = {"app/db.py", "app/main.py", "web/x.ts", "README.md", "tools/old.py"}
CODE = {"app/db.py", "app/main.py", "web/x.ts", "tools/old.py"}
CHANGES = [  # newest first
    ["app/db.py", ".env"],
    ["app/db.py", "web/x.ts"],
    ["app/main.py", "app/db.py", "README.md"],
    [".env"],
    [],
]


def area(page_id: str) -> Page:
    return Page(page_id, {"kind": "area", "title": page_id})


def test_files_and_folders_are_counted_once_per_commit_and_only_allowed_ones_named() -> None:
    churn = measure_churn(CHANGES, ALLOWED, CODE, set(), [])
    assert churn.commits == 5
    assert churn.files == [HotSpot("app/db.py", 3), HotSpot("README.md", 1), HotSpot("app/main.py", 1),
                           HotSpot("web/x.ts", 1)]  # fmt: skip
    assert churn.folders == [HotSpot("app", 3), HotSpot(".", 1), HotSpot("web", 1)]
    assert all(".env" not in spot.name for spot in churn.files + churn.folders)


def test_areas_count_commits_in_their_scope_with_their_rationale() -> None:
    app, web, empty = area("areas/app"), area("areas/web"), area("areas/empty")
    churn = measure_churn(CHANGES, ALLOWED, CODE, set(), [(app, ["app"], 1, 4), (web, ["web/"], 2, 0),
                                                          (empty, ["docs"], 0, 0)])  # fmt: skip
    assert churn.areas == [AreaSpot(app, 3, 1, 4), AreaSpot(web, 1, 2, 0), AreaSpot(empty, 0, 0, 0)]


def test_quiet_code_is_code_no_recent_commit_changed_by_folder() -> None:
    churn = measure_churn(CHANGES, ALLOWED, CODE, {"app/db.py", "web/x.ts", "README.md"}, [])
    assert churn.quiet == [HotSpot("app", 1), HotSpot("tools", 1)]
    assert churn.quiet_files == 2


def test_commit_size_counts_every_file_excluded_ones_too() -> None:
    churn = measure_churn(CHANGES, ALLOWED, CODE, set(), [])
    assert (churn.median_files, churn.p90_files) == (2, 2.6)  # sizes 2, 2, 3, 1, 0


def test_the_90th_percentile_never_passes_the_largest_commit() -> None:
    churn = measure_churn([["a"], ["a"], ["a"], [str(number) for number in range(20)]], set(), set(), set(), [])
    assert churn.p90_files is not None and churn.p90_files <= 20


def test_one_commit_gives_its_own_size_and_none_give_nothing() -> None:
    one = measure_churn([["a", "b"]], {"a", "b"}, set(), set(), [])
    assert (one.median_files, one.p90_files) == (2, 2)
    none = measure_churn([], ALLOWED, CODE, set(), [])
    assert (none.commits, none.files, none.median_files, none.p90_files) == (0, [], None, None)
    assert none.quiet_files == len(CODE)
