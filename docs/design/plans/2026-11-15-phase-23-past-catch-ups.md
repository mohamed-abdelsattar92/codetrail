# Phase 23: past catch-ups, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a tech lead goes back to catch-ups they already read, one day or a stretch, on one free page with a calendar.

**Architecture:** every catch-up now records the digests it wrote, in a new `catch_ups.digests` column; older ones are matched to digests by the time they ran. A new module, `codetrail.team.history`, parses the page's dates, builds the calendar's weeks and assembles each past catch-up from `team.db`, the overviews' files and the targets' guides, reusing the team page's digest summaries and overview rendering. One `GET /team/catch-ups` route on the team server renders it, server-side, with no script.

**Tech Stack:** Python 3.14, FastAPI/Starlette, Jinja2, SQLite, pytest, Playwright. Nothing new.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, section 25 whole, and what it points to: 24.7 to 24.9 (the team page, catching up, the overview), 20.3 (how the Repository page's calendar is drawn), 7.4 (the security model), 8.3 (read marks). Read section 25 before any task.

## Global Constraints

- AGENTS.md applies whole: test first; the plainest code, reusing what exists; no new dependency; never push, never `--no-verify`. Commits use Conventional Commits with a scope (`team`, `config`, `web`, `docs`), body sections What, Why, Alternatives considered, Risks, Agent and model, body lines of at most 100 characters, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; commit with `git commit -F <file>`.
- Configuration, exact values (25.5): under `[team]`, `history_days = 7` (1 to 366) and `max_history_catch_ups = 50` (1 to 500).
- The calendar spans 53 weeks, Monday first, ending with the week of `calendar`; a day's shade is `heat-<min(count, 4)>` (none, 1, 2, 3, 4 or more), using the existing `.heat-0` to `.heat-4` classes and `.calendar` styles (`static/css/components.css`).
- Days are UTC days. A catch-up is listed and counted only when its record has at least one ticked target (`targets` not `[]`), on the UTC date of its `finished_at`.
- The page is one `GET` route behind `SecurityMiddleware` (7.4). It reads only: no assistant call, no write to `team.db` or any guide, no fetch, no source listing.
- Overviews and digests stay in English (`lang="en" dir="ltr"`, decision 7); every other string goes through gettext with an Arabic translation (`just catalogs`). Untrusted text (repository and target names, titles, failure reasons) is autoescaped and wrapped in `<bdi>`, as on the team page.
- People are never counted or ranked (24.1); the calendar shades catch-ups only.

## Review Focus

1. **Corrupt rows in `team.db`:** a `digests`, `targets`, `failed` or `unticked` column holding JSON that isn't the expected shape (hand-edited) leaves that catch-up out or treats the value as empty; the page still renders the others. Test in Task 3.
2. **Many catch-ups:** 500 finished catch-ups in the calendar's span render with the list capped at `max_history_catch_ups` and the note of how many were left out, while the calendar still counts all 500. Test in Task 3.
3. **Dates at the edge of what Python holds:** `calendar=0001-01-01`, `to=9999-12-31` or a `calendar` whose **Later** link would pass the year 9999 never raise `OverflowError`; every date must lie between `1970-01-01` and `9998-12-31`, or the page is a 400. Test in Task 2.
4. **A catch-up whose guide moved on:** an older catch-up (no recorded digests) whose target's guide was removed, or whose digest pages were rewritten by later updates, still renders, with only the digests that exist and match. Test in Task 3.
5. **The Arabic page:** labels in Arabic, dates in Western digits, the calendar left to right, overviews and digests in English. Test in Task 4.

## Decisions this plan makes inside the spec (for the founder to check)

- **Dates are written as ISO dates** (`2026-11-12`), as everywhere else in Codetrail, so the range reads "2026-11-10 to 2026-11-16" and a square's label "2026-11-12: 2 catch-ups", not "12 November 2026". Section 25.3 is updated to match in Task 5.
- **Accepted dates lie between 1970-01-01 and 9998-12-31,** so moving the calendar 53 weeks either way can't overflow.
- **The calendar is an inline SVG rendered by the template,** each linked day an `<a>` around its `<rect>`, with the same sizes as `repository.js` draws (cells of 14, squares of 11, month and Monday, Wednesday, Friday labels from `month_names` and `weekday_names`).
- **The overview's "unread" pill** reads `overview_reads`; a digest's reads its target's `LearningState`, as the cards do.

---

### Task 1: Configuration, the recorded digests and the range query

**Files:**
- Create: `src/codetrail/database/migrations/0008_catch_up_digests.sql`
- Modify: `src/codetrail/config.py` (`TeamSettings`)
- Modify: `src/codetrail/team/state.py` (`CatchUpRecord`, `TeamState.finish`, new `TeamState.finished_between`)
- Modify: `src/codetrail/team/catch_up.py` (the `state.finish` call in `run_catch_up`)
- Modify: `docs/configuration.md`, or wherever `[team]` is documented (`grep -rln "max_overview_words" docs`)
- Test: `tests/unit/test_team_state.py`, `tests/unit/test_config.py` (or the file that tests `TeamSettings`), `tests/api/test_team_catch_up.py`

**Interfaces:**
- Produces:
  - `TeamSettings.history_days: int` and `TeamSettings.max_history_catch_ups: int`.
  - `CatchUpRecord.digests: list[tuple[str, str]] | None`, each `(target, digest id)` in the order written; `None` when the catch-up recorded none (before this phase). The field goes last, after `unticked`.
  - `TeamState.finish(catch_up_id, targets, overview_id, overview_problem, failed=None, unticked=None, digests=None)`, where `digests: Sequence[tuple[str, str]] | None` is stored as a JSON list of pairs (`[]` when given and empty).
  - `TeamState.finished_between(first: date, last: date) -> list[CatchUpRecord]`: finished catch-ups whose `finished_at` UTC date lies in `first..last`, both included, with at least one ticked target, newest first (by `finished_at`, then `id`). A row whose JSON columns don't parse or aren't the expected shapes is left out (Review Focus 1).

- [ ] **Step 1: Write the failing tests**
  - `test_history_settings_defaults_and_ranges`: defaults 7 and 50; `history_days` 0 and 367 refused, 1 and 366 accepted; `max_history_catch_ups` 0 and 501 refused, 1 and 500 accepted.
  - `test_a_finished_catch_up_records_its_digests`: `finish(..., digests=[("acme-web-shop", "digests/2026-11-11-abc123")])` reads back as `[("acme-web-shop", "digests/2026-11-11-abc123")]`; a finish without `digests` reads back `None`.
  - `test_finished_between_lists_ticked_catch_ups_by_day_newest_first`: with `_now` monkeypatched, catch-ups finished at `2026-11-10T23:59:59+00:00`, `2026-11-11T00:00:00+00:00` and `2026-11-12T08:00:00+00:00`; `finished_between(date(2026, 11, 11), date(2026, 11, 12))` returns the last two, newest first; a catch-up started `2026-11-10T23:58:00+00:00` and finished `2026-11-11T00:01:00+00:00` is on 11 November.
  - `test_finished_between_leaves_out_declined_and_corrupt_rows`: one finished with `targets=[]`, one whose `digests` column is set by SQL to `'{"x": 1}'`, one whose `targets` is `'not json'`: none listed; a good one beside them is.
  - In `tests/api/test_team_catch_up.py`, `test_a_catch_up_records_the_digests_it_wrote`: the existing two-target catch-up with the fake assistant leaves `last_finished().digests` equal to the two `(target, digest id)` pairs its updates wrote, in the targets' order.

- [ ] **Step 2: Run them and see them fail**
  Run: `mise exec -- uv run pytest -q tests/unit/test_team_state.py tests/unit/test_config.py tests/api/test_team_catch_up.py -p no:cacheprovider`
  Expected: the new tests FAIL (unknown fields, unexpected keyword `digests`, no `finished_between`).

- [ ] **Step 3: Implement**
  - Migration `0008_catch_up_digests.sql`: `ALTER TABLE catch_ups ADD COLUMN digests TEXT;` with a comment that NULL means not recorded (catch-ups from before design section 25).
  - `finished_between` queries `finished_at >= first.isoformat() AND finished_at < (last + 1 day).isoformat() AND targets != '[]'` (ISO strings compare in time order), and parses rows through the same helper `last_finished` uses, which now returns `None` for a row it can't parse.
  - In `run_catch_up`, pass `digests=[(digest.target, digest.digest_id) for digest in digests]` from the `_digests(paths, targets)` it already computes.

- [ ] **Step 4: Run the tests and see them pass**, then `mise exec -- just lint` and `mise exec -- just typecheck`.

- [ ] **Step 5: Commit** — `feat(team): record the digests each catch-up wrote, and list catch-ups by day`

### Task 2: The page's dates and the calendar

**Files:**
- Create: `src/codetrail/team/history.py`
- Test: `tests/unit/test_team_history.py`

**Interfaces:**
- Produces:
  - `HistoryRange(first: date, last: date, calendar: date)`, a frozen dataclass.
  - `parse_range(from_text: str, to_text: str, calendar_text: str, today: date, default_days: int, gettext: Callable[[str], str]) -> HistoryRange`; raises `RangeError(message)` (a `ValueError`) whose message, translated through `gettext`, names the problem. The tests pass `gettext=lambda text: text`.
  - `CalendarDay(day: date, count: int, in_range: bool, future: bool)` and `calendar_weeks(end: date, counts: Mapping[date, int], chosen: HistoryRange, today: date) -> list[list[CalendarDay]]`: 53 lists of 7, Monday first, the last week holding `end`.
  - `HistoryRange.earlier() -> HistoryRange` and `.later(today: date) -> HistoryRange | None`: `calendar` moved 371 days back, or forward to at most `today`, the range kept; `later(today)` is `None` once `calendar`'s week holds `today` or a later day.
  - `history_query(chosen: HistoryRange, day: date | None = None) -> str`: the `?from=…&to=…&calendar=…` for the page's links (a day's link sets `from` and `to` to it and keeps `calendar`).

- [ ] **Step 1: Write the failing tests**
  - `test_the_default_range_is_the_last_history_days_ending_today`: `parse_range("", "", "", date(2026, 11, 15), 7)` is `HistoryRange(date(2026, 11, 9), date(2026, 11, 15), date(2026, 11, 15))`.
  - `test_a_missing_end_takes_its_default`: only `to="2026-11-01"` gives `first=2026-10-26`, `calendar=2026-11-01`; only `from="2026-11-10"` gives `last=today`; `calendar` alone keeps the default range.
  - `test_dates_refused` (parametrized): `2026-02-30`, `2026-1-05`, `"٢٠٢٦-١١-١٠"` (Arabic-Indic digits), `2026-11-10T00:00`, `from` after `to`, a range of 3661 days, `0001-01-01`, `1969-12-31`, `9999-12-31` each raise `RangeError`; a range of exactly 3660 days, both ends included, is accepted.
  - `test_a_future_to_is_allowed`: `to=2026-12-31` with today 2026-11-15 parses.
  - `test_the_calendar_ends_with_calendar_s_week`: for `calendar=2026-11-12` (a Thursday), the last week starts Monday 2026-11-09, the first Monday 2025-11-10, 53 weeks of 7; days after today are `future=True`.
  - `test_the_calendar_marks_the_range_and_counts`: `counts={date(2026, 11, 11): 2}` gives that day `count=2`, and the days 2026-11-09 to 2026-11-15 `in_range=True`, the rest `False`.
  - `test_moving_the_calendar_keeps_the_range`: `earlier()` moves only `calendar` back 371 days; `later(date(2026, 11, 15))` from a calendar in today's week is `None`; from 2025-11-12 it moves to 2026-11-15 (371 days would pass today); from 2024-11-12 it moves to 2025-11-18.
  - `test_a_day_s_link_keeps_the_calendar`: `history_query(range, date(2026, 11, 11))` is `?from=2026-11-11&to=2026-11-11&calendar=2026-11-12`.

- [ ] **Step 2: Run them and see them fail**
  Run: `mise exec -- uv run pytest -q tests/unit/test_team_history.py -p no:cacheprovider`
  Expected: FAIL with `ModuleNotFoundError: codetrail.team.history`.

- [ ] **Step 3: Implement** in `history.py`, with dates read by `re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text, re.ASCII)` then `date.fromisoformat`. Bounds as module constants `EARLIEST = date(1970, 1, 1)`, `LATEST = date(9998, 12, 31)` and `MAX_RANGE_DAYS = 3660` (from section 25.6); `calendar` must lie within the same bounds.

- [ ] **Step 4: Run the tests and see them pass**, then lint and typecheck.

- [ ] **Step 5: Commit** — `feat(team): parse the past catch-ups page's dates and build its calendar`

### Task 3: Past catch-ups, assembled

**Files:**
- Modify: `src/codetrail/team/history.py`
- Modify: `src/codetrail/team/cards.py` (expose `digest_summaries`; move `Overview` and the overview reading here)
- Modify: `src/codetrail/team/server.py` (`last_catch_up` uses `load_overview`; `Overview` imported from `cards`)
- Test: `tests/unit/test_team_history.py`, `tests/unit/test_team_cards.py`

**Interfaces:**
- Consumes: `TeamState.finished_between`, `CatchUpRecord.digests` (Task 1); `HistoryRange` (Task 2).
- Produces:
  - In `cards.py`: `digest_summaries(target: str, pages: list[Page], max_nodes: int) -> list[DigestSummary]` (what `_card` does with `BASE_PATH` set to `/repos/<target>`; `_card` calls it); `Overview` (moved unchanged from `server.py`); `load_overview(overviews: Overviews, overview_id: str) -> Overview | None` (the reading and rendering now in `server.last_catch_up`, returning `None` for a missing, invalid or unparsable overview, logging as it does today).
  - In `history.py`:
    - `PastRepository(target: str, repository: str, followed: bool, digests: list[DigestSummary], unread: set[str])`.
    - `PastCatchUp(record: CatchUpRecord, overview: Overview | None, overview_unread: bool, repositories: list[PastRepository], failed: dict[str, str], unticked: list[str])`.
    - `past_catch_ups(paths: Paths, name: str, chosen: HistoryRange, followed: Mapping[str, tuple[str, str]], max_nodes: int, limit: int) -> tuple[list[PastCatchUp], int, dict[date, int]]`: the catch-ups in the range (at most `limit`, newest first), how many it left out, and the counts per day over the calendar's 53 weeks. `followed` is `followed_targets(...)`'s result (target → repository, branch). A team with no `team.db` gives `([], 0, {})` without creating one.
    - `digests_of(paths: Paths, record: CatchUpRecord) -> dict[str, list[Page]]`: the digest pages per target, from `record.digests` when recorded, else matched by time (25.4): for each ticked target not in `record.failed`, its guide's digest pages whose `written_at` string lies between `started_at` and `finished_at`. Ids that fail `PAGE_ID`, targets that fail `TARGET_NAME`, and pages the guide doesn't have are left out; a target whose guide folder is missing gives no pages, without raising.

- [ ] **Step 1: Write the failing tests** (each builds a team with `tests.api.test_team_server.following_team` and writes rows and pages as `tests/api/test_team_page.py`'s helpers do)
  - `test_recorded_digests_are_shown_in_order`.
  - `test_an_older_catch_up_s_digests_are_matched_by_time`: a digest written inside the window is found; one written a second after `finished_at`, and one of a target in `failed`, are not.
  - `test_a_recorded_digest_the_guide_doesn_t_have_is_left_out`, and one whose id is `../../etc/passwd`.
  - `test_a_target_no_longer_followed_keeps_its_target_name`: `followed=False`, `repository` equal to the target name, no digests.
  - `test_a_removed_guide_shows_no_digests` (Review Focus 4).
  - `test_the_overview_and_its_read_mark`: an overview marked read in `overview_reads` has `overview_unread=False`; its segments match what the team page renders.
  - `test_an_overview_id_that_fails_the_pattern_reads_as_none`.
  - `test_the_list_is_capped_and_the_calendar_counts_all`: 500 finished catch-ups over 50 days with `limit=50` give 50 catch-ups, 450 left out, and counts summing to 500 (Review Focus 2).
  - `test_a_team_that_never_caught_up_makes_no_database`.
  - In `test_team_cards.py`, `test_digest_summaries_link_into_the_target_s_guide`: a summary's `link` is `/repos/acme-web-shop/pages/<id>`.
  - The team page's existing overview tests in `tests/api/test_team_page.py` still pass unchanged.

- [ ] **Step 2: Run them and see them fail**
  Run: `mise exec -- uv run pytest -q tests/unit/test_team_history.py tests/unit/test_team_cards.py -p no:cacheprovider`

- [ ] **Step 3: Implement.** `past_catch_ups` reads `finished_between(calendar's first Monday, calendar's Sunday)` once for the counts, and `finished_between(chosen.first, chosen.last)` for the list. Read marks: `TeamState.overview_read` and, per target with a database, `LearningState(connection).unread_digests(ids)`, opening each target's database once per page.

- [ ] **Step 4: Run the tests and see them pass**, then `mise exec -- uv run pytest -q tests/api/test_team_page.py -p no:cacheprovider`, lint and typecheck.

- [ ] **Step 5: Commit** — `feat(team): assemble past catch-ups from their records, overviews and digests`

### Task 4: The page, its link and the sidebar

**Files:**
- Create: `src/codetrail/web/templates/catch_ups.html`
- Modify: `src/codetrail/team/server.py` (route `GET /team/catch-ups`)
- Modify: `src/codetrail/web/templates/team.html` (the header line's link), `src/codetrail/web/templates/layout/sidebar.html` (the team pages' list)
- Modify: `src/codetrail/web/static/css/components.css` (the outlined range days, linked days' focus ring, the folding sections if no existing class fits: look for the Documentation page's folding sections first)
- Modify: the catalogs (`just catalogs`, then each new Arabic entry by hand; fix any fuzzy entry)
- Test: `tests/api/test_team_catch_ups_page.py`

**Interfaces:**
- Consumes: `parse_range`, `calendar_weeks`, `history_query`, `HistoryRange.earlier/later` (Task 2); `past_catch_ups` (Task 3); `month_names`, `weekday_names` (`codetrail.web.repository_view`).
- Produces: `GET /team/catch-ups?from=&to=&calendar=`, each a `str` query parameter with `max_length=10` and default `""` (`from` through `Query(alias="from")`); the template's `active` is `"catch-ups"`.

- [ ] **Step 1: Write the failing tests** in `tests/api/test_team_catch_ups_page.py`, with the `team` fixture of `tests/api/test_team_page.py`:
  - `test_a_read_overview_still_shows_here`: after `POST /team/overview/read`, the team page has no overview, and `/team/catch-ups` shows it with no "unread" pill.
  - `test_one_day_and_a_stretch`: one day's page has its catch-ups' sections open (`<details open`); a stretch has only the newest open.
  - `test_overview_skipped_missing_and_failed`: no "Across the team" for a skipped one, the `overview_problem` text for a failed one, nothing for a missing file.
  - `test_the_rest_line_lists_failed_and_unticked_only`.
  - `test_a_bad_date_is_a_400_that_names_it`: `from=2026-02-30` is 400 and the body says which date; `to=<script>` is 400, and the response never holds `<script>` unescaped; an 11-character `to` is refused by the query's length limit (422) before parsing.
  - `test_the_calendar_links_days_with_catch_ups`: the day with a catch-up is an `<a href="/team/catch-ups?from=2026-11-11&amp;to=2026-11-11&amp;calendar=…">` with an `aria-label` of `2026-11-11: 1 catch-up`; a day without one has no link; **Later** is absent when the calendar holds today.
  - `test_earlier_moves_the_calendar_and_keeps_the_range`, and `test_a_range_past_the_calendar_says_so`: a range starting before the calendar's first day carries the note beside the calendar.
  - `test_the_cap_s_note`: with `max_history_catch_ups = 1` and two catch-ups in the range, the page lists one and says one was left out and that a narrower range shows it.
  - `test_the_team_page_links_here_once_a_catch_up_finished`: no link before any catch-up; the link after one; the sidebar lists it after **Add repositories** on every team page.
  - `test_the_page_needs_the_session`: without the session cookie it redirects to login as the team page does; with a foreign `Host` it is refused.
  - `test_the_page_spends_and_writes_nothing`: no assistant call (the fake records none), `team.db`'s and each guide's modification times unchanged.
  - `test_the_arabic_page` (Review Focus 5): with the language set to Arabic, the title is Arabic, the dates are Western digits, the calendar's container has `dir="ltr"`, and the overview's container `lang="en"`.
  - `test_untrusted_names_are_escaped`: a repository name and a failure reason of `<img src=x onerror=alert(1)>` appear only escaped, inside `<bdi>`.
  - `test_no_catch_ups_in_the_range` and `test_no_catch_ups_at_all`: their messages, with the calendar still shown in the first.

- [ ] **Step 2: Run them and see them fail**
  Run: `mise exec -- uv run pytest -q tests/api/test_team_catch_ups_page.py -p no:cacheprovider`
  Expected: FAIL with 404 for the route.

- [ ] **Step 3: Implement.** The route parses with `parse_range(..., today=datetime.now(UTC).date(), default_days=settings.team.history_days, gettext=_)`, answers a `RangeError` with a 400 page (the existing `error.html`, as other team routes' errors render), then renders `catch_ups.html` through `render(...)`. The template reuses `team.html`'s digest and overview markup: move each into a macro in `macros.html` (`team_digest(digest, target)` without the Mark read button when a `read_button=False` argument is given, and `team_overview(overview)`) and call them from both pages, so the two stay the same. The date form is `<form method="get" action="/team/catch-ups">` with two `<input type="date" name="from|to">`, without `calendar`, so showing a new range moves the calendar to it. The calendar's counts also render as a folded `<details>` table of the days with catch-ups.

- [ ] **Step 4: Run the tests and see them pass**, then `mise exec -- uv run pytest -q tests/api -p no:cacheprovider`, lint and typecheck, and `mise exec -- just catalogs` with no fuzzy entries left.

- [ ] **Step 5: Commit** — `feat(team): the past catch-ups page, with a calendar, linked from the team page`

### Task 5: In the browser, and the documents

**Files:**
- Modify: `tests/browser/test_team_page.py` (or a new `tests/browser/test_team_catch_ups_page.py` beside it, using its `two_guides` fixture)
- Modify: `tests/browser/test_guide_screenshots.py` (one new screenshot of a week)
- Modify: `docs/guides/teams.md` ("Going back to past catch-ups", with the screenshot), `README.md` (current state), `CHANGELOG.md` (**Unreleased**, Added), `docs/design/2026-10-05-codetrail-design.md` (Phase 23 row to "(built)"; 25.3's range and labels as ISO dates)
- Create: the screenshot under `docs/images/guides/`, the next `teams-NN-` number

- [ ] **Step 1: Write the failing browser tests**
  - `test_past_catch_ups_page` in both themes: from the team page, the **Past catch-ups** link opens the page; clicking a linked day shows that day; the form shows a stretch; Tab reaches each folding section's summary and Enter opens and closes it; the accessibility scan passes; no console errors.
  - At phone width (375 px), the page has no horizontal scroll outside the calendar's own scroll box.

- [ ] **Step 2: Run them and see them fail**
  Run: `mise exec -- uv run pytest -q -m browser tests/browser/test_team_page.py -p no:cacheprovider`

- [ ] **Step 3: Make them pass** (fix markup and CSS only; behaviour is Task 4's), then add the screenshot to `test_guide_screenshots.py` and regenerate: `CODETRAIL_SCREENSHOTS=<scratch dir> mise exec -- uv run pytest -q -m browser tests/browser/test_guide_screenshots.py -p no:cacheprovider`, copying in only the new image and any team image that changed because of the new link, and checking that the images carry no metadata chunks.

- [ ] **Step 4: Update the documents** listed above.

- [ ] **Step 5: Run `mise exec -- just ci`.** Expected: every check passes.

- [ ] **Step 6: Commit** — `docs(team): past catch-ups in the teams guide, with a screenshot` (tests and documents; split the browser tests into their own `test(team)` commit if they needed markup fixes).

### After the tasks

Review the branch with the `security-reviewer` agent against `docs/security/review-checklist.md`, fix every finding (lows included), and finish it into `develop` with the review's verdict in the merge message (AGENTS.md, Workflow). The founder pushes.
