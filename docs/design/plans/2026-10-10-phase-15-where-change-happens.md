# Phase 15: where change happens implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show where a target repository changes most (hot spots by file, top-level folder and guide area, beside each area's documented and inferred rationale), its quiet code and its commit size, on the Repository page, at no cost.

**Architecture:** `repo` gains two reads: the files each of the latest non-merge commits changed (one `git log -z --name-only`), and the files changed since a date. A new `metrics/churn.py` turns them, with the allowed files and the guide's area pages, into a `Churn`; `RepositoryReport` carries it, and the Repository page shows a "Where change happens" section. Only paths in the current allowed files are ever named.

**Tech Stack:** Python 3.14, git, FastAPI with Jinja templates, pytest, Playwright. No new dependency.

**Spec:** [design section 19](../2026-10-05-codetrail-design.md#19-repository-statistics) (19.1 "Where change happens", 19.3, 19.6, 19.7), decision 14 in [brainstorm-decisions.md](../brainstorm-decisions.md).

## Global Constraints

- Nothing in this phase calls an assistant or spends anything.
- Only `repo` reads the mirror. A path is named only when it is in the current `source.json` allowed files; commit counts may include other files, never their names.
- No commit message, author or email is read by the new git calls: they print author times and file names only.
- Configuration: `[metrics] churn_window = 500` and `quiet_days = 365` globally.
- Interface text goes through the gettext catalogs; Arabic gets every new string; compiled catalogs are committed.
- Every commit: Conventional Commits with a scope and the What, Why, Alternatives considered, Risks, Agent and model sections.

## Review Focus

1. **A file name with spaces, newlines or non-UTF-8 bytes in a commit:** parsing never misreads the next commit; an unallowed name is never shown. Test in Task 1.
2. **A commit that changes only excluded files** (`.env`): it counts toward commit size but names nothing. Test in Task 2.
3. **No guide pages yet:** hot spots by file and folder still show; areas say they appear once the guide is written. Test in Task 3.
4. **A window larger than the history, or an empty repository:** no division by zero; median and 90th percentile with one commit are that commit's size. Test in Task 2.
5. **An area page with unusable front matter:** it is skipped, as the coverage metric skips it. Test in Task 2.

---

### Task 1: settings and the history reads

**Files:** `src/codetrail/config.py`, `src/codetrail/repo/history.py`; tests `tests/unit/test_config.py`, `tests/unit/test_history.py`.

**Interfaces — produces:** `MetricsSettings.churn_window: int = 500` (gt 0, le 100_000), `MetricsSettings.quiet_days: int = 365` (gt 0); `changed_files(mirror: Path, end: str, limit: int) -> list[list[str]]` (each of the latest `limit` non-merge commits' changed paths, newest first); `files_changed_since(mirror: Path, end: str, since: date) -> set[str]`.

- [ ] Failing tests: `changed_files` over a repository whose commits change `a b/x.txt` (a space), `y`, nothing (an empty commit) and a merge returns `[[], ["y"], ["a b/x.txt", "y"]]` for the non-merge commits, newest first, and respects `limit`; `files_changed_since` returns only paths changed by commits after the date (fixture dates are minutes after 2026-09-21). Config defaults `(500, 365)`.
- [ ] Implement `changed_files` with one call: `git log -n<limit> --no-merges -z --name-only --format=%x01 --end-of-options <end>`. Parse: split on NUL; a field that is exactly `\x01` (after stripping one leading newline) starts a commit; other non-empty fields, with one leading newline stripped from the first after a header, are its paths (decoded with `surrogateescape`, like `changed_paths`). `files_changed_since`: `git log --since=<iso date> -z --name-only --format= --end-of-options <end>`, the non-empty names.
- [ ] Commit: `feat(repo): read the files each latest commit changed`

### Task 2: `metrics/churn.py`

**Interfaces — consumes:** Task 1; `page_scope` (`metrics/coverage.py`), `in_scope` (`generate/outline.py`), `measure_rationale(...).pages`. **Produces:** `HotSpot(name: str, commits: int)`, `AreaSpot(page: Page, commits: int, documented: int, inferred: int)`, `Churn(commits: int, files: list[HotSpot], folders: list[HotSpot], areas: list[AreaSpot], quiet: list[HotSpot], quiet_files: int, median_files: float | None, p90_files: float | None)`; `measure_churn(changes: Sequence[Sequence[str]], allowed: Collection[str], code: Collection[str], recently_changed: Collection[str], areas: Sequence[tuple[Page, OutlineEntry, int, int]]) -> Churn`.

- [ ] Failing tests (pure, no git): files counted once per commit, only allowed ones named, sorted by commits then name; folders are the first path segment (root files as `.`), a commit counting once per folder; areas count a commit once when any changed allowed file is in the area's scope paths, carrying its documented and inferred counts; quiet code is the code files not in `recently_changed`, grouped by folder with counts, `quiet_files` their total; median and 90th percentile of files per commit over all commits (excluded files included in the counts), None with no commits; one commit gives its own size for both.
- [ ] Implement with `collections.Counter` and `statistics.median` / `statistics.quantiles(n=10)[8]` (two or more commits; one commit uses its size).
- [ ] Commit: `feat(metrics): measure hot spots, quiet code and commit size`

### Task 3: the report and the page

**Files:** `src/codetrail/metrics/repository.py`, `src/codetrail/web/app.py`, `templates/repository.html`, catalogs; tests `tests/integration/test_repository_report.py`, `tests/api/test_repository_page.py`.

- [ ] Failing tests: the report over the fixture target has `churn` with `app/db.py` the hottest file and `app` the hottest folder, never `.env` though a commit changes it; with a written guide (the fake assistant), the area `areas/app` shows its commit count and its documented and inferred blocks; `/repository` shows a `#change` section listing them, says areas appear once the guide is written when there are no pages, and never names an excluded path; when the history read fails, the section says it couldn't be read and the rest renders. The page's cache key adds the guide's head and uncommitted state, as the Documentation page's does.
- [ ] Implement: `RepositoryReport.churn: Churn | None`, built in its own `try` from `changed_files`, `files_changed_since(today - quiet_days)`, the manifest's allowed files and code files (`size.largest` paths), and the area pages with `page_scope` and `measure_rationale`. The section: hot files (capped by `largest_files`), folders, areas as a table (area, commits, documented, inferred) sorted by commits, with a note that much change and mostly inferred rationale marks where an ADR or README would help most; quiet code by folder; commit size. Arabic for every new string.
- [ ] Commit: `feat(web): show where change happens on the Repository page`

### Task 4: docs and the whole suite

- [ ] README (Phase 15 done, unreleased), CHANGELOG line, getting-started sentence, design 18.8's hot spots item marked done.
- [ ] `just ci`; security review; finish into `develop`.
