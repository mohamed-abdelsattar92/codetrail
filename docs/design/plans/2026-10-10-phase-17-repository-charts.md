# Phase 17: the Repository page's charts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Repository page into the approved design: an interactive timeline, an activity calendar, a change heat map with an area-by-month grid, and charts for commit size, languages, commit types and facts, in both themes and in every interface language.

**Architecture:** The report gains the data the charts need (commits per day, each latest commit's time with its files, lines per area, folder and file, the area-by-month grid, the size histogram). A view module turns the report into one JSON document with translated labels and plural templates; the template embeds it in a `<script type="application/json">` block and renders every chart's numbers as tables. A new module, `static/js/repository.js`, draws the charts from the JSON with SVG and HTML, setting repository text only as text.

**Tech Stack:** Python 3.14, FastAPI with Jinja, Babel (plural rules, month names), plain ES modules, Playwright. No new dependency.

**Spec:** [design section 20](../2026-10-05-codetrail-design.md#20-the-repository-pages-charts), building on section 19; the approved design is [the Claude Design canvas](https://claude.ai/artifact/RTb1VGd8Gd3o9jw4uW1ZS8).

## Global Constraints

- No assistant call, no new route, no new dependency; the security policy is unchanged (no inline script).
- The JSON block escapes `<`, `>`, `&`, U+2028 and U+2029; the script sets repository text with `textContent` or `setAttribute` only.
- Only allowed files are named (section 19.7).
- Colors are tokens on `:root`, redefined for dark mode; the categorical colors are exactly the four validated ones per theme; amber appears only as the mostly-inferred mark.
- Every label goes through the catalogs; Arabic gets every new string; digits stay Western.
- Configuration: `metrics.folder_depth = 2`, `metrics.heatmap_tiles = 60`.
- Commits: Conventional Commits with a scope and the What, Why, Alternatives considered, Risks, Agent and model sections.

## Review Focus

1. **A repository younger than the range** (a week-old repository on "2 years"): the window starts at the first commit, nothing divides by zero, the pan buttons are disabled. Test in Task 4.
2. **A tag note or path holding `</script>`, `<!--` or markup:** the JSON stays inside its block and the page shows the text. Tests in Tasks 2 and 3.
3. **A target with no guide areas, no tags or no dependency changes:** each lane or map says so instead of drawing nothing. Tests in Tasks 3 and 4.
4. **Arabic:** labels translated with the right plural form, Western digits, charts left to right. Test in Task 5.
5. **More folders or files than `heatmap_tiles`:** the largest are drawn and a note counts the rest. Test in Task 1.

---

### Task 1: the data

**Files:** `src/codetrail/config.py`, `src/codetrail/repo/history.py`, `src/codetrail/metrics/activity.py`, `src/codetrail/metrics/churn.py`, `src/codetrail/metrics/repository.py`; tests in `tests/unit/test_history.py`, `tests/unit/test_metrics_activity.py`, `tests/unit/test_metrics_churn.py`, `tests/integration/test_repository_report.py`, `tests/unit/test_config.py`.

**Interfaces — produces:**
- `MetricsSettings.folder_depth: int = 2` (1–6), `.heatmap_tiles: int = 60` (1–1000).
- `changed_files(mirror, end, limit) -> list[tuple[int, list[str]]]`: each commit's author time and its paths, newest first (format `%x01%at`).
- `Activity.days: list[int]` (commits per UTC day) and `Activity.days_start: date | None` (the first day), from the first commit read to `today`.
- `Churn` gains `folders` at `folder_depth` levels; `HeatTile(name: str, path: str, lines: int, commits: int)` lists `area_tiles` (with `AreaSpot`'s documented and inferred), `folder_tiles`, `file_tiles`, each capped at `heatmap_tiles` by lines, with `left_out: dict[str, int]`; `area_months: list[tuple[str, list[int | None]]]` (area title, the last 12 months' counts, None before the oldest commit read) with `months: list[str]` ("YYYY-MM"); `size_histogram: list[int]` over the buckets 1, 2, 3, 4–5, 6–10, 11–20, 21+.
- `measure_churn(changes, allowed, code_lines: Mapping[str, int], recently_changed, areas, today, folder_depth, tile_cap)`; `code_lines` maps each code file to its lines (replacing the `code` set).

- [ ] Failing tests: `changed_files` returns times beside paths; `measure_activity` gives `days` with zeros on quiet days and ends at today; folders at depth 2 (`services/api/x.py` → `services/api`, a root file → `.`); lines per area sum their in-scope code files; a level with more tiles than the cap keeps the largest by lines and counts the rest; the area-by-month grid counts a commit once per area and month and marks months before the oldest commit as None; the histogram's buckets; the integration report carries all of it.
- [ ] Implement, run, commit `feat(metrics): gather the data the Repository page's charts draw`.

### Task 2: the view model

**Files:** create `src/codetrail/web/repository_view.py`; test `tests/unit/test_repository_view.py`.

**Interfaces — produces:**
- `plural_templates(language: Language, singular: str, plural: str) -> dict[str, str]`: for each plural category of the language (Babel's `Locale.parse(code).plural_form`, plus `other`), `ngettext(singular, plural, n)` with `n` the smallest number in that category, its `%(count)s` replaced by `{count}`.
- `chart_document(report: RepositoryReport, history: Mapping[Metric, Sequence[Value]], language: Language, today: date, listed: int) -> dict[str, object]`: numbers and repository text the script draws, plus `labels` (plain translated strings, plural templates, month names from `babel.dates.get_month_names("abbreviated")`, the language code for `Intl`).
- `script_json(document: Mapping[str, object]) -> str`: `json.dumps(..., ensure_ascii=False, separators=(",", ":"))` with `<`, `>`, `&`, U+2028, U+2029 as `\u` escapes.

- [ ] Failing tests: English plural templates are `{"one": "{count} commit", "other": "{count} commits"}`; Arabic has six categories, each from its own form; `script_json` of a note `</script><script>alert(1)</script><!--` contains no `<` and round-trips through `json.loads`; the document holds the days, releases with notes, dependencies with dates, the watch start, the tiles at each level with their left-out counts, the grid, the histogram, languages (four, then Other), types, facts with trends, the tiles' trends.
- [ ] Implement, run, commit `feat(web): build the Repository page's chart data with translated labels`.

### Task 3: the template

**Files:** `src/codetrail/web/templates/repository.html`, `src/codetrail/web/app.py`, `src/codetrail/web/static/css/components.css`; test `tests/api/test_repository_page.py`.

- [ ] Failing tests: the page carries `<script type="application/json" data-repository-data>` whose content parses to the document; a hostile tag note stays inside it as escaped text; the containers `data-chart="timeline"`, `"calendar"`, `"heatmap"`, `"grid"`, `"sizes"`, `"languages"` exist; each chart's table holds its numbers (the timeline's months, the calendar's active weeks, the heat map's tiles, the grid's cells); empty states say there are no releases, no areas yet, no dependency changes.
- [ ] Implement: the page's sections in the design's order and wording, the tables in `<details>`, the range and level pickers as buttons, the tokens (`--s1`…`--s5`, `--h0`…`--h4`, `--band`) in light and dark; the route passes `chart_document` through `script_json`.
- [ ] Run the API tests; commit `feat(web): lay out the Repository page for its charts`.

### Task 4: the script

**Files:** create `src/codetrail/web/static/js/repository.js`; `src/codetrail/web/static/js/main.js`; test `tests/browser/test_repository_charts.py`.

- [ ] Failing browser tests: the timeline draws one bar per month on All time and redraws per week on 1 year; hovering a bar and focusing it with the arrow keys show its period and count; the pan buttons move the window and disable at the ends; choosing a release shows its notes; the calendar draws 53 weeks; the heat map draws tiles for areas, then folders and files when switched, and choosing a tile updates the side panel; a young repository's window starts at its first commit; no console errors.
- [ ] Implement `setUpRepository()`: read and parse the JSON, then draw each chart into its container with `document.createElementNS` for SVG and `textContent` for every string; squarified treemap; `Intl.PluralRules` and the label templates; `Intl.NumberFormat(code + "-u-nu-latn")`.
- [ ] Run `just test-browser`; commit `feat(web): draw the Repository page's charts`.

### Task 5: language and accessibility

**Files:** catalogs; `tests/browser/test_accessibility.py`, `tests/browser/test_languages.py`.

- [ ] Failing tests: the Arabic page draws the charts with Arabic month names and labels, Western digits, left to right; the accessibility scan passes on `/repository` in both themes with the charts drawn.
- [ ] Extract and translate every new string; compile; run; commit `feat(web): translate the Repository page's charts`.

### Task 6: docs and the whole suite

- [ ] README and getting-started describe the charts; CHANGELOG line; `just ci`; reviews; finish.
