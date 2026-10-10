# Phase 18: the Documentation page's charts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redraw the Documentation page as an overview column beside four folding sections, each with a chart for its numbers and its items, filter chips on the two long lists, and the section most in need open on load.

**Architecture:** Everything is drawn on the server, as the Repository page draws its charts that only show numbers: inline SVG built from numbers, with the Repository page's classes (`.stack-bar`, `.stack-legend`, `.share-bar`, `.hbars`, `.heat-N`, `.series-fill-N`). A few pure helpers in `web/documentation.py` derive what the template needs (the section to open, each item's kind and the folded counts per kind, a stacked bar's segments, an ADR's age). The folding sections are `<details>`; one small module, `static/js/documentation.js`, runs the filter chips and moves no repository text.

**Tech Stack:** Python 3.14, FastAPI with Jinja (i18n extension), plain ES modules, Playwright. No new dependency, route, setting or stored value.

**Spec:** [design section 21](../2026-10-05-codetrail-design.md#21-the-documentation-pages-charts), building on section 18; the Repository page's charts (section 20) are the model to match.

## Global Constraints

- No assistant call, no new route, no new setting, no new dependency, no JSON block; the security policy is unchanged (no inline script).
- Every repository and guide text is rendered by autoescaping templates; the SVG is built from numbers only; `documentation.js` sets only `hidden`, `aria-pressed` and the chips' own text.
- Documented is indigo, inferred is amber, and amber appears nowhere else: the four documented sources take `.heat-4` to `.heat-1`; body without a why `--series-other`; subject only `--line-strong`.
- Every color is a token on `:root`, redefined for dark mode; reduced motion turns off transitions (the `--duration` token already does).
- Charts run left to right in every language (`dir="ltr"` on each chart); digits stay Western.
- Every new label goes through the catalogs; Arabic gets every new string.
- Long lists show `metrics.max_listed` items, the rest in `<details class="more">`.
- Commits: Conventional Commits with a scope and the What, Why, Alternatives considered, Risks, Agent and model sections; run commands through `mise exec --` in an agent's shell.

## Review Focus

1. **Every share at 0% on a fresh target** (the browser fixture: no documented rationale, no commit with a why, nothing mentioned): the tie goes to the section highest on the page, so Documented rationale opens, and nothing else does. Test in Task 1 (`test_a_tie_opens_the_section_higher_on_the_page`).
2. **A filter whose kind has nothing folded away:** the folded rest's `<details>` hides rather than reading "0 more". Tests in Tasks 2 and 3.
3. **The commit history unreadable:** the commits section says so, draws no bar and no chips, its tile has no bar, and the section can't be chosen to open. Tests in Tasks 1 and 2.
4. **A page title or commit subject holding markup** (`<b>`, `</details>`): it shows as text and doesn't close a folding section. Test in Task 2.
5. **Only one kind in a list** (every commit subject-only): no chips are rendered, since one chip and All would show the same list. Test in Task 2.

---

### Task 1: the page's helpers

**Files:**
- Modify: `src/codetrail/metrics/decisions.py` (rename `_date` to `written_on`, public)
- Modify: `src/codetrail/web/documentation.py`
- Test: `tests/unit/test_documentation_view.py`

**Interfaces:**
- Produces, in `codetrail.metrics.decisions`: `written_on(decision: Entity) -> date | None` (the ADR's `Date:`, or None).
- Produces, in `codetrail.web.documentation`:
  - `SECTIONS: tuple[tuple[str, Metric], ...]`, the sections a share can open, in page order: `("rationale", DOCUMENTED_SHARE)`, `("commits", COMMIT_WHY_SHARE)`, `("coverage", MENTIONED_SHARE)`.
  - `section_to_open(values: Mapping[Metric, Value]) -> str | None`: `"decisions"` when `ADR_ATTENTION` is above 0; else the id of the section whose share is lowest, among shares that were measured and are below 1, the first in page order on a tie; else None.
  - `commit_kind(commit: Commit) -> str`: `"body-only"` when `commit.body` is truthy, else `"subject-only"` (the same test `measure_commits` uses).
  - `Filter(kinds: list[str], totals: dict[str, int], folded: dict[str, int])` (frozen dataclass) and `filtered(kinds: Sequence[str], listed: int) -> Filter`. `totals` has `"all"` first, then each kind in order of first appearance; `folded` has the same keys, counting the items past the first `listed`.
  - `segments(parts: Sequence[tuple[str, int, str]], width: int = 1000) -> list[tuple[str, float, float]]`: each part `(tone, count, name)` with a count above 0 as `(tone, x, width)` in a `width`-wide frame, rounded to one decimal; nothing when the total is 0. A part whose tone is `""` takes its room but isn't returned (the empty rest of a share).
  - `days_proposed(decision: Entity, today: date) -> int | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_documentation_view.py`)

```python
from codetrail.facts import Entity, EntityKind
from codetrail.repo.history import Commit
from codetrail.web.documentation import commit_kind, days_proposed, filtered, section_to_open, segments


def commit(body: str) -> Commit:
    return Commit("abc1234", "Someone", "2026-10-01", "fix: x", body, [], False)


def test_an_adr_needing_attention_opens_the_decisions() -> None:
    values = {Metric.ADR_ATTENTION: Value(1, None), Metric.DOCUMENTED_SHARE: Value(0, 9)}
    assert section_to_open(values) == "decisions"


def test_otherwise_the_lowest_share_opens() -> None:
    values = {Metric.ADR_ATTENTION: Value(0, None), Metric.DOCUMENTED_SHARE: Value(3, 4),
              Metric.COMMIT_WHY_SHARE: Value(1, 4), Metric.MENTIONED_SHARE: Value(2, 4)}  # fmt: skip
    assert section_to_open(values) == "commits"


def test_a_tie_opens_the_section_higher_on_the_page() -> None:
    values = {Metric.ADR_ATTENTION: Value(0, None), Metric.DOCUMENTED_SHARE: Value(0, 3),
              Metric.COMMIT_WHY_SHARE: Value(0, 2), Metric.MENTIONED_SHARE: Value(0, 2)}  # fmt: skip
    assert section_to_open(values) == "rationale"


def test_what_wasnt_measured_or_is_complete_never_opens() -> None:
    # No pages (nothing to measure), the history unreadable (no commit share), everything mentioned.
    values = {Metric.ADR_ATTENTION: Value(0, None), Metric.DOCUMENTED_SHARE: Value(0, 0),
              Metric.MENTIONED_SHARE: Value(5, 5)}  # fmt: skip
    assert section_to_open(values) is None


def test_a_commits_kind_follows_its_body() -> None:
    assert commit_kind(commit("Just a body.")) == "body-only"
    assert commit_kind(commit("")) == "subject-only"


def test_a_filter_counts_each_kind_and_its_folded_rest() -> None:
    found = filtered(["body-only", "subject-only", "subject-only", "body-only"], listed=2)
    assert found.kinds == ["body-only", "subject-only", "subject-only", "body-only"]
    assert found.totals == {"all": 4, "body-only": 2, "subject-only": 2}
    assert list(found.totals) == ["all", "body-only", "subject-only"]
    assert found.folded == {"all": 2, "body-only": 1, "subject-only": 1}


def test_a_kind_with_nothing_folded_counts_zero() -> None:
    assert filtered(["package", "project"], listed=1).folded == {"all": 1, "package": 0, "project": 1}
    assert filtered([], listed=50).totals == {"all": 0}


def test_segments_share_the_frame_and_leave_out_empty_parts() -> None:
    parts = [("heat-4", 1, "ADRs"), ("heat-3", 0, "Documents"), ("tone-inferred", 3, "Inferred")]
    assert segments(parts) == [("heat-4", 0.0, 250.0), ("tone-inferred", 250.0, 750.0)]
    assert segments([("heat-4", 0, "ADRs")]) == []


def test_the_empty_rest_of_a_share_takes_room_but_isnt_drawn() -> None:
    assert segments([("series-fill-1", 1, ""), ("", 2, "")]) == [("series-fill-1", 0.0, 333.3)]


def test_an_adrs_age_in_days() -> None:
    dated = Entity("decision:ADR-0002", EntityKind.DECISION, {"date": "2026-09-01"})
    assert days_proposed(dated, date(2026, 10, 10)) == 39
    assert days_proposed(Entity("decision:ADR-0003", EntityKind.DECISION, {}), date(2026, 10, 10)) is None
```

- [ ] **Step 2: Run them and see them fail**

Run: `mise exec -- uv run pytest tests/unit/test_documentation_view.py -q`
Expected: FAIL with `ImportError: cannot import name 'commit_kind'`.

- [ ] **Step 3: Rename the ADR date reader** in `src/codetrail/metrics/decisions.py`

```python
def written_on(decision: Entity) -> date | None:
    """The ADR's `Date:`, or None when it gives none Python can read."""
    try:
        return date.fromisoformat(str(decision.attributes.get("date", "")))
    except ValueError:
        return None
```

and in `measure_decisions`, `written = written_on(decision)`.

- [ ] **Step 4: Add the helpers** to `src/codetrail/web/documentation.py` (imports: `from collections import Counter`, `Sequence` is already imported, `from codetrail.facts import Entity`, `from codetrail.metrics.decisions import written_on`, `from codetrail.repo.history import Commit`); update the module docstring to "The Documentation page's tiles, its sections' helpers, and the report kept in memory between updates (design sections 18.4 and 21)."

```python
# The sections a share can open, in their order on the page (design section 21.1).
SECTIONS = (
    ("rationale", Metric.DOCUMENTED_SHARE),
    ("commits", Metric.COMMIT_WHY_SHARE),
    ("coverage", Metric.MENTIONED_SHARE),
)


def section_to_open(values: Mapping[Metric, Value]) -> str | None:
    """The decisions when an ADR needs attention; else the lowest measured share below 1, the first on a tie."""
    attention = values.get(Metric.ADR_ATTENTION)
    if attention is not None and attention.numerator:
        return "decisions"
    shares = []
    for order, (section, metric) in enumerate(SECTIONS):
        value = values.get(metric)
        if value is not None and value.share is not None and value.share < 1:
            shares.append((value.share, order, section))
    return min(shares)[2] if shares else None


def commit_kind(commit: Commit) -> str:
    """A commit without a why: it has a body that doesn't say why, or only a subject."""
    return "body-only" if commit.body else "subject-only"


@dataclass(frozen=True)
class Filter:
    kinds: list[str]  # each item's kind, in the list's order
    totals: dict[str, int]  # items per kind, "all" first
    folded: dict[str, int]  # items past the first `listed`, per kind, "all" first


def filtered(kinds: Sequence[str], listed: int) -> Filter:
    """What a list's filter chips need: each item's kind, and the items of each kind in all and in the folded rest."""
    rest = Counter(kinds[listed:])
    totals = {"all": len(kinds), **Counter(kinds)}
    folded = {kind: len(kinds[listed:]) if kind == "all" else rest.get(kind, 0) for kind in totals}
    return Filter(list(kinds), totals, folded)


def segments(parts: Sequence[tuple[str, int, str]], width: int = 1000) -> list[tuple[str, float, float]]:
    """A stacked bar's parts as tone, x and width; a part with no tone takes its room and isn't drawn."""
    total = sum(count for _, count, _ in parts)
    drawn, x = [], 0.0
    for tone, count, _ in parts:
        if count <= 0 or not total:
            continue
        span = count / total * width
        if tone:
            drawn.append((tone, round(x, 1), round(span, 1)))
        x += span
    return drawn


def days_proposed(decision: Entity, today: date) -> int | None:
    written = written_on(decision)
    return (today - written).days if written is not None else None
```

- [ ] **Step 5: Run the tests and see them pass**

Run: `mise exec -- uv run pytest tests/unit/test_documentation_view.py tests/unit/test_metrics_decisions.py -q`
Expected: PASS.

- [ ] **Step 6: Lint, type-check and commit**

Run: `mise exec -- just lint && mise exec -- just typecheck`

```bash
git add src/codetrail/metrics/decisions.py src/codetrail/web/documentation.py tests/unit/test_documentation_view.py
git commit  # feat(web): derive what the Documentation page's sections need
```

The body names the four helpers (What), design section 21.3 (Why), computing them in the template (Alternatives), none (Risks).

---

### Task 2: the page

**Files:**
- Modify: `src/codetrail/web/templates/documentation.html` (rewrite)
- Modify: `src/codetrail/web/app.py:511-547` (the `/documentation` route)
- Modify: `src/codetrail/web/static/css/components.css` (after the `/* The Documentation page (design section 18.4). */` block)
- Test: `tests/api/test_documentation_page.py`

**Interfaces:**
- Consumes: Task 1's `section_to_open`, `commit_kind`, `filtered`, `Filter`, `segments`, `days_proposed`.
- Produces (for Task 3's script and tests): each metric's `<section id="rationale|commits|decisions|coverage">` holds one `<details class="doc-fold">`, `open` on at most one; a filterable list sits in `<div data-filterable>` with its chips `<div class="segmented filter-chips" data-filter hidden>` of `<button data-show="all|<kind>" aria-pressed>`, a `<p data-filter-status aria-live="polite">`, items `<li data-kind="<kind>">`, and its folded rest's summary as one `<span data-more="<kind>" data-count="<n>">` per kind, all but `all` hidden. The overview is `<aside class="doc-overview">`.

- [ ] **Step 1: Write the failing API tests** (append to `tests/api/test_documentation_page.py`, which already imports `GlobalConfig`, `SecretScanner`, `CodetrailError` and `add_commit`)

```python
def section(page: str, name: str) -> str:
    """One metric's section of the page, from its id to its end."""
    return page.split(f'<section id="{name}"', 1)[1].split("</section>", 1)[0]


def client_listing(paths: Paths, listed: int) -> TestClient:
    session = SessionState(60)
    settings = GlobalConfig.model_validate({"metrics": {"max_listed": listed}})
    client = TestClient(create_app(paths, "shop", session, settings), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def test_the_section_most_in_need_opens_and_no_other(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)  # ADR 0002 has been proposed since 2020
    page = client_for(paths).get("/documentation").text
    assert page.count('<details class="doc-fold" open>') == 1
    assert '<details class="doc-fold" open>' in section(page, "decisions")
    assert 'class="doc-overview"' in page


def test_each_chart_prints_its_numbers(paths: Paths) -> None:
    run_update(paths, "shop", claude=FakeAssistant(plans=[PLAN], page_writer=page_writer))
    page = client_for(paths).get("/documentation").text
    rationale, commits = section(page, "rationale"), section(page, "commits")
    assert 'aria-label="ADRs: 1, Documents: 0, Code comments: 0, Commit messages: 0, Inferred: 1"' in rationale
    assert 'href="/pages/areas/app"' in rationale and "1 / 1" in rationale  # the page's bar: documented / inferred
    assert 'aria-label="Explain why: 1, Body without a why: 1, Subject only: 1"' in commits
    decisions = section(page, "decisions")
    assert ">accepted<" in decisions and ">proposed<" in decisions
    assert "Explained by a guide page" in section(page, "coverage")


def test_every_item_carries_its_kind_and_the_chips_start_hidden(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/documentation").text
    commits, coverage = section(page, "commits"), section(page, "coverage")
    assert 'data-kind="body-only"' in commits and 'data-kind="subject-only"' in commits
    assert 'data-filter hidden' in commits
    assert 'data-show="body-only" aria-pressed="false"' in commits
    assert 'data-kind="package"' in coverage
    assert "data-filter hidden" not in coverage  # only packages go unmentioned: one kind needs no chips


def test_a_long_list_counts_its_folded_rest_per_kind(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    commits = section(client_listing(paths, 1).get("/documentation").text, "commits")
    # Newest first: "fix: c" (a body, no why) shows; "Commit 0" (a subject only) folds away.
    assert '<span data-more="all" data-count="1">1 more</span>' in commits
    assert '<span data-more="body-only" data-count="0" hidden>' in commits
    assert '<span data-more="subject-only" data-count="1" hidden>' in commits


def test_markup_in_a_commit_subject_stays_text(paths: Paths, tmp_path: Path) -> None:
    add_commit(tmp_path / "target", {"app/db.py": "x = 5\n"}, "fix: close </details><b>bold</b>")
    run_update(paths, "shop", facts_only=True)
    commits = section(client_for(paths).get("/documentation").text, "commits")
    assert "&lt;/details&gt;&lt;b&gt;bold&lt;/b&gt;" in commits
    assert "<b>bold</b>" not in commits


def test_without_the_history_the_commits_draw_nothing(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    run_update(paths, "shop", facts_only=True)

    def broken(self: SecretScanner, text: str) -> object:
        raise CodetrailError("gitleaks isn't installed")

    monkeypatch.setattr(SecretScanner, "scan_text", broken)
    commits = section(client_for(paths).get("/documentation").text, "commits")
    assert "stack-bar" not in commits and "data-filterable" not in commits
```

- [ ] **Step 2: Run them and see them fail**

Run: `mise exec -- uv run pytest tests/api/test_documentation_page.py -q`
Expected: the new tests FAIL (no `doc-fold`, no `data-kind`); the existing ones pass.

- [ ] **Step 3: Pass the helpers from the route** in `src/codetrail/web/app.py` (import `commit_kind, days_proposed, filtered, section_to_open, segments` from `codetrail.web.documentation`); replace the route's final `return render(...)`:

```python
        values, listed = report.values(), settings.metrics.max_listed
        without_why = report.commits.without_why if report.commits else []
        return render(
            "documentation.html", active="documentation", report=report, tiles=build_tiles(values, before, history),
            listed=listed, today=today, open_section=section_to_open(values),
            commit_filter=filtered([commit_kind(commit) for commit in without_why], listed),
            fact_filter=filtered([str(entity.kind) for entity in report.mentions.unmentioned], listed),
            segments=segments, days_proposed=days_proposed,
        )  # fmt: skip
```

- [ ] **Step 4: Rewrite `src/codetrail/web/templates/documentation.html`**

```jinja
{% extends "base.html" %}
{% block title %}{{ _("Documentation") }}{% endblock %}
{% block content %}
{# A list of `listed` items and its folded rest; with a filter, each item carries its kind and the rest's summary
   is written once per kind, so the chips can show the right count without the script translating anything. #}
{% macro listing(items, filter=none) -%}
<ul class="list" lang="en" dir="ltr">{% for item in items[:listed] %}<li{% if filter %} data-kind="{{ filter.kinds[loop.index0] }}"{% endif %}>{{ caller(item) }}</li>{% endfor %}</ul>
{% if items|length > listed %}
<details class="more">
  <summary>{% if filter %}{% for kind, count in filter.folded.items() %}<span data-more="{{ kind }}" data-count="{{ count }}"{% if kind != "all" %} hidden{% endif %}>{% trans count=count %}{{ count }} more{% endtrans %}</span>{% endfor %}{% else %}{% trans count=items|length - listed %}{{ count }} more{% endtrans %}{% endif %}</summary>
  <ul class="list" lang="en" dir="ltr">{% for item in items[listed:] %}<li{% if filter %} data-kind="{{ filter.kinds[listed + loop.index0] }}"{% endif %}>{{ caller(item) }}</li>{% endfor %}</ul>
</details>
{% endif %}
{%- endmacro %}
{# Chips only when the list holds two kinds or more: with one, a chip would show what All shows. #}
{% macro chips(filter, names) -%}
{% if filter.totals|length > 2 %}
<div class="segmented filter-chips" role="group" aria-label="{{ _('Show') }}" data-filter hidden>
  <button type="button" data-show="all" aria-pressed="true">{{ _("All") }} · {{ filter.totals["all"]|number }}</button>
  {% for kind, label in names %}{% if filter.totals.get(kind) %}<button type="button" data-show="{{ kind }}" aria-pressed="false">{{ label }} · {{ filter.totals[kind]|number }}</button>{% endif %}{% endfor %}
</div>
<p class="visually-hidden" data-filter-status aria-live="polite"></p>
{% endif %}
{%- endmacro %}
{# A stacked bar with its legend; parts are (tone, count, name), and the bar's text alternative names each count. #}
{% macro stack(parts) -%}
<div class="stack-bar" dir="ltr" role="img" aria-label="{% for tone, count, name in parts %}{{ name }}: {{ count }}{% if not loop.last %}, {% endif %}{% endfor %}"><svg viewBox="0 0 1000 20" preserveAspectRatio="none" aria-hidden="true">{% for tone, x, width in segments(parts) %}<rect class="{{ tone }}" x="{{ x }}" y="0" height="20" width="{{ [width - 2, 0]|max|round(1) }}"/>{% endfor %}</svg></div>
<ul class="stack-legend">{% for tone, count, name in parts %}<li><span class="swatch {{ tone }}" aria-hidden="true"></span>{{ name }}<span class="faint">{{ count|number }}</span></li>{% endfor %}</ul>
{%- endmacro %}
{# A tile's small bar: the tile already says its numbers in words. #}
{% macro mini(parts) -%}
<div class="share-bar" dir="ltr" aria-hidden="true"><svg viewBox="0 0 1000 10" preserveAspectRatio="none">{% for tone, x, width in segments(parts) %}<rect class="{{ tone }}" x="{{ x }}" y="0" height="10" width="{{ width }}"/>{% endfor %}</svg></div>
{%- endmacro %}
{% macro hbar(label, count, widest, value, tone="series-fill-1") -%}
<li><span class="hbar-label">{{ label }}</span><svg class="hbar-track" viewBox="0 0 100 12" preserveAspectRatio="none" aria-hidden="true"><rect class="{{ tone }}" x="0" y="0" height="12" width="{{ (count / widest * 100)|round(1) if widest else 0 }}" rx="2"/></svg><span class="hbar-value">{{ value }}</span></li>
{%- endmacro %}
{% macro page_bar(row, widest) -%}
{% set documented = row.documented / widest * 100 %}
<li><a class="hbar-label" href="/pages/{{ row.page.id }}" lang="en" dir="ltr">{{ row.page.title }}</a><svg class="hbar-track" viewBox="0 0 100 12" preserveAspectRatio="none" role="img" aria-label="{% trans documented=row.documented, inferred=row.inferred %}{{ documented }} documented, {{ inferred }} inferred{% endtrans %}"><rect class="tone-documented" x="0" y="0" height="12" width="{{ documented|round(1) }}"/><rect class="tone-inferred" x="{{ documented|round(1) }}" y="0" height="12" width="{{ (row.inferred / widest * 100)|round(1) }}"/></svg><span class="hbar-value">{{ row.documented }} / {{ row.inferred }}</span></li>
{%- endmacro %}
{% macro fact_link(entity) -%}
<a class="grow" href="/facts/{{ entity.id }}">{{ entity.id }}</a>
{%- endmacro %}
{% macro decision_link(decision) -%}
<a class="grow" href="/facts/{{ decision.id }}">{{ decision.attributes.get("number", "") }} · {{ decision.attributes.get("title", "") }}</a>{% if decision.attributes.get("date") %}<span class="faint">{{ decision.attributes.date }}</span>{% endif %}
{%- endmacro %}
{% macro share(value) -%}
{% if value.denominator %}{% trans numerator=value.numerator, denominator=value.denominator %}{{ numerator }} of {{ denominator }}{% endtrans %}{% else %}{{ _("nothing to measure yet") }}{% endif %}
{%- endmacro %}
{% macro about(text) -%}
<details class="doc-about"><summary>{{ _("What this counts") }}</summary><p class="muted">{{ text }}</p></details>
{%- endmacro %}
{% macro tile_card(tile, label, title_id, parts=none) %}
<section class="stat metric" aria-labelledby="{{ title_id }}">
  <span class="card-label" id="{{ title_id }}">{{ label }}</span>
  {% if tile.value is none %}
    <span class="stat-value">—</span><span class="stat-note">{{ _("Not available just now.") }}</span>
  {% elif tile.metric == "adr_attention" %}
    <span class="stat-value{% if tile.value.numerator %} behind{% endif %}">{{ tile.value.numerator }}</span>
    <span class="stat-note">{{ _("proposed too long, or retired and still cited") }}</span>
  {% elif tile.metric == "documented_share" and not tile.value.denominator %}
    <span class="stat-value">—</span><span class="stat-note">{{ _("No pages yet: the guide's rationale is measured once your assistant writes it.") }}</span>
    <button type="button" class="button small" data-update-button>{{ _("Update the guide") }}</button>
  {% else %}
    <span class="stat-value">{% if tile.percent is not none %}{{ tile.percent }}%{% else %}—{% endif %}</span>
    <span class="stat-note">{{ share(tile.value) }}</span>
    {% if parts %}{{ mini(parts) }}{% endif %}
  {% endif %}
  {% if tile.value is not none and (tile.value.denominator or tile.metric == "adr_attention") %}
  <span class="stat-note">{% if tile.change is none %}{{ _("No earlier update to compare with.") }}{% elif tile.metric == "adr_attention" %}{% trans change="%+d"|format(tile.change) %}{{ change }} since the last update{% endtrans %}{% else %}{% trans change="%+d"|format(tile.change) %}{{ change }} points since the last update{% endtrans %}{% endif %}</span>
  {% endif %}
  {% if tile.points %}
  {% set count = tile.trend_text.split(", ")|length %}
  <svg class="trend" viewBox="0 0 120 32" role="img" aria-label="{% trans count=count, values=tile.trend_text %}Trend over {{ count }} update: {{ values }}{% pluralize %}Trend over {{ count }} updates: {{ values }}{% endtrans %}"><polyline points="{{ tile.points }}"/></svg>
  {% endif %}
</section>
{% endmacro %}
<div class="wide">
<header class="page-head">
  <h1>{{ _("Documentation") }}</h1>
  <p class="muted">{{ _("How well the repository explains itself: its guide's rationale, its commits, its decisions and its documents. Measured from the code and the guide, without calling your assistant.") }}</p>
</header>
{% if report is none %}
<section class="empty">
  {% if unavailable %}
  <h2>{{ _("The metrics aren't available just now") }}</h2>
  <p>{{ _("Codetrail couldn't read the target's data; the log says why.") }}</p>
  {% else %}
  <h2>{{ _("No facts yet") }}</h2>
  <p>{{ _("Update the guide once: reading the code is free, and the metrics come from it.") }}</p>
  <button type="button" class="button primary" data-update-button>{{ _("Update the guide") }}</button>
  {% endif %}
</section>
{% else %}
{% set rationale, commits, decisions = report.rationale, report.commits, report.decisions %}
{% set mentions, explained, has_pages = report.mentions, report.explained, report.rationale.pages %}
<div class="doc-layout">
<aside class="doc-overview" aria-label="{{ _('Overview') }}">
  {{ tile_card(tiles["documented_share"], _("Documented rationale"), "tile-documented",
               [("tone-documented", rationale.documented, ""), ("tone-inferred", rationale.inferred, "")]) }}
  {{ tile_card(tiles["commit_why_share"], _("Commits that explain why"), "tile-commits",
               [("series-fill-1", commits.explains_why, ""), ("", commits.measured - commits.explains_why, "")] if commits else none) }}
  {{ tile_card(tiles["adr_attention"], _("ADRs needing attention"), "tile-adrs") }}
  {{ tile_card(tiles["mentioned_share"], _("Mentioned in a document"), "tile-mentioned",
               [("series-fill-1", mentions.mentioned|length, ""), ("", mentions.unmentioned|length, "")]) }}
</aside>
<div class="doc-sections">

<section id="rationale" aria-labelledby="rationale-title">
<details class="doc-fold"{% if open_section == "rationale" %} open{% endif %}>
  <summary><h2 id="rationale-title">{{ _("Documented rationale") }}</h2><span class="doc-todo">{% if not has_pages %}{{ _("No pages yet") }}{% elif rationale.inferred %}{% trans count=rationale.inferred %}{{ count }} inferred, worth documenting{% pluralize %}{{ count }} inferred, worth documenting{% endtrans %}{% else %}{{ _("Nothing inferred") }}{% endif %}</span></summary>
  <div class="doc-body">
  {{ about(_("Rationale blocks in the guide's area and concept pages: documented ones quote a source Codetrail checked, inferred ones are the assistant's reading. It counts only what the guide wrote about.")) }}
  {% if not has_pages %}
  <p class="muted">{{ _("No pages yet: the guide's rationale is measured once your assistant writes it.") }}</p>
  {% else %}
  {{ stack([("heat-4", rationale.by_source.adr, _("ADRs")), ("heat-3", rationale.by_source.docs, _("Documents")),
            ("heat-2", rationale.by_source.code, _("Code comments")), ("heat-1", rationale.by_source.commit, _("Commit messages")),
            ("tone-inferred", rationale.inferred, _("Inferred"))]) }}
  <h3>{{ _("By page, most inferred first") }}</h3>
  {% set ns = namespace(widest=1) %}{% for row in rationale.pages %}{% set ns.widest = [ns.widest, row.documented + row.inferred]|max %}{% endfor %}
  <ul class="hbars page-bars" dir="ltr">{% for row in rationale.pages[:listed] %}{{ page_bar(row, ns.widest) }}{% endfor %}</ul>
  {% if rationale.pages|length > listed %}
  <details class="more"><summary>{% trans count=rationale.pages|length - listed %}{{ count }} more{% endtrans %}</summary><ul class="hbars page-bars" dir="ltr">{% for row in rationale.pages[listed:] %}{{ page_bar(row, ns.widest) }}{% endfor %}</ul></details>
  {% endif %}
  {% if rationale.inferred_blocks %}
  <h3>{{ _("Inferred: worth documenting") }}</h3>
  {% call(block) listing(rationale.inferred_blocks) %}<a class="grow" href="/pages/{{ block.page.id }}">{{ block.first_line }}</a><span class="faint">{{ block.page.title }}</span>{% endcall %}
  {% endif %}
  {% endif %}
  </div>
</details>
</section>

<section id="commits" aria-labelledby="commits-title">
<details class="doc-fold"{% if open_section == "commits" %} open{% endif %}>
  <summary><h2 id="commits-title">{{ _("Commits that explain why") }}</h2><span class="doc-todo">{% if commits is none %}{{ _("Not available just now.") }}{% else %}{% trans count=commits.without_why|length, measured=commits.measured %}{{ count }} without a why, of the latest {{ measured }}{% pluralize %}{{ count }} without a why, of the latest {{ measured }}{% endtrans %}{% endif %}</span></summary>
  <div class="doc-body">
  {% if commits is none %}
  <p class="muted">{{ _("The commit history couldn't be read just now; the log says why.") }}</p>
  {% else %}
  {{ about(_("The latest commits on the branch, merges left out. A commit explains why when its body matches the target's commit_why_pattern.")) }}
  {{ stack([("tone-documented", commits.explains_why, _("Explain why")), ("tone-other", commits.body_only, _("Body without a why")),
            ("tone-quiet", commits.subject_only, _("Subject only"))]) }}
  {% if commits.withheld %}<p class="muted">{% trans count=commits.withheld %}{{ count }} message withheld: gitleaks flagged it{% pluralize %}{{ count }} messages withheld: gitleaks flagged them{% endtrans %}</p>{% endif %}
  {% if commits.without_why %}
  <h3>{{ _("Without a why") }}</h3>
  <div data-filterable>
  {{ chips(commit_filter, [("body-only", _("Body without a why")), ("subject-only", _("Subject only"))]) }}
  {% call(commit) listing(commits.without_why, commit_filter) %}<code>{{ commit.sha[:7] }}</code><span class="grow">{{ commit.subject }}</span><span class="faint">{% if commit.body %}{{ _("body without a why") }}{% else %}{{ _("subject only") }}{% endif %}</span>{% endcall %}
  </div>
  {% endif %}
  {% endif %}
  </div>
</details>
</section>

<section id="decisions" aria-labelledby="decisions-title">
{% set to_check = decisions.undated|length + (decisions.uncited|length if has_pages else 0) %}
<details class="doc-fold"{% if open_section == "decisions" %} open{% endif %}>
  <summary><h2 id="decisions-title">{{ _("Architecture decisions") }}</h2><span class="doc-todo">{% if not decisions.by_status %}{{ _("None found") }}{% else %}{% trans count=decisions.attention %}{{ count }} needs attention{% pluralize %}{{ count }} need attention{% endtrans %}{% if to_check %} · {% trans count=to_check %}{{ count }} to check{% pluralize %}{{ count }} to check{% endtrans %}{% endif %}{% endif %}</span></summary>
  <div class="doc-body">
  {% if not decisions.by_status %}
  <p class="muted">{{ _("No architecture decision records were found where the target's settings say they live.") }}</p>
  {% else %}
  {{ about(_("A proposed ADR needs attention once it waits longer than the target's settings allow, and a superseded or deprecated one while a guide page still cites it. Worth a look: proposed ADRs with no date, and accepted ones no page cites.")) }}
  <div class="doc-columns">
  <div>
  <h3>{{ _("By status") }}</h3>
  {% set widest = decisions.by_status.values()|map("length")|max %}
  <ul class="hbars" lang="en" dir="ltr">{% for status, found in decisions.by_status|dictsort %}{{ hbar(status, found|length, widest, found|length|number, "series-fill-5" if status in ("superseded", "deprecated") else "series-fill-1") }}{% endfor %}</ul>
  </div>
  <div>
  {% if decisions.overdue or decisions.superseded_cited %}
  <h3>{{ _("Needs attention") }}</h3>
  {% if decisions.overdue %}
  <h4>{{ _("Proposed for too long") }}</h4>
  {% call(decision) listing(decisions.overdue) %}{{ decision_link(decision) }}<span class="faint">{% trans count=days_proposed(decision, today) %}proposed {{ count }} day ago{% pluralize %}proposed {{ count }} days ago{% endtrans %}</span>{% endcall %}
  {% endif %}
  {% if decisions.superseded_cited %}
  <h4>{{ _("Superseded or deprecated, still cited by the guide") }}</h4>
  {% call(decision) listing(decisions.superseded_cited) %}{{ decision_link(decision) }}{% endcall %}
  {% endif %}
  {% endif %}
  {% if to_check %}
  <h3>{{ _("Worth a look") }}</h3>
  {% if decisions.undated %}
  <h4>{{ _("Proposed, with no date") }}</h4>
  {% call(decision) listing(decisions.undated) %}{{ decision_link(decision) }}{% endcall %}
  {% endif %}
  {% if decisions.uncited and has_pages %}
  <h4>{{ _("Accepted, but no page cites them") }}</h4>
  {% call(decision) listing(decisions.uncited) %}{{ decision_link(decision) }}{% endcall %}
  {% endif %}
  {% endif %}
  </div>
  </div>
  {% endif %}
  </div>
</details>
</section>

<section id="coverage" aria-labelledby="coverage-title">
{% set mention_total = mentions.mentioned|length + mentions.unmentioned|length %}
<details class="doc-fold"{% if open_section == "coverage" %} open{% endif %}>
  <summary><h2 id="coverage-title">{{ _("Mentioned and explained") }}</h2><span class="doc-todo">{% trans count=mentions.unmentioned|length %}{{ count }} never mentioned{% pluralize %}{{ count }} never mentioned{% endtrans %}{% if has_pages %} · {% trans count=explained.unexplained|length %}{{ count }} unexplained{% pluralize %}{{ count }} unexplained{% endtrans %}{% endif %}</span></summary>
  <div class="doc-body">
  {{ about(_("A package counts when a document names it as a whole word, a project when its folder holds a README or a document names its folder, and a resource when a document names its address. A fact is explained when it falls in the scope of a written area or concept page.")) }}
  <p class="muted">{{ _("External packages, projects and infrastructure resources that some document names. A mention isn't a reason: this only shows what no document talks about at all.") }}</p>
  <ul class="hbars coverage-bars" dir="ltr">
    {% set value %}{% trans numerator=mentions.mentioned|length, denominator=mention_total %}{{ numerator }} of {{ denominator }}{% endtrans %}{% endset %}
    {{ hbar(_("Mentioned in a document"), mentions.mentioned|length, mention_total, value) }}
    {% if has_pages %}
    {% set value %}{% trans numerator=explained.explained, denominator=explained.total %}{{ numerator }} of {{ denominator }}{% endtrans %}{% endset %}
    {{ hbar(_("Explained by a guide page"), explained.explained, explained.total, value) }}
    {% endif %}
  </ul>
  {% if not has_pages %}
  <p class="muted">{{ _("Which facts the guide explains, and which ADRs it cites, are measured once your assistant has written its pages.") }}</p>
  {% endif %}
  <div class="doc-columns">
  {% if mentions.unmentioned %}
  <div>
  <h3>{{ _("Not mentioned in any document") }}</h3>
  <div data-filterable>
  {{ chips(fact_filter, [("package", _("Packages")), ("project", _("Projects")), ("resource", _("Resources"))]) }}
  {% call(entity) listing(mentions.unmentioned, fact_filter) %}{{ fact_link(entity) }}{% endcall %}
  </div>
  </div>
  {% endif %}
  {% if explained.unexplained and has_pages %}
  <div>
  <h3>{{ _("Not explained by any page") }}</h3>
  {% call(entity) listing(explained.unexplained) %}{{ fact_link(entity) }}{% endcall %}
  </div>
  {% endif %}
  </div>
  </div>
</details>
</section>

</div>
</div>
{% endif %}
</div>
{% endblock %}
```

- [ ] **Step 5: Add the styles** to `src/codetrail/web/static/css/components.css`, after the `.trend polyline` rule

```css
/* The Documentation page's layout (design section 21): the overview beside its folding sections. */
.doc-layout {
  display: grid;
  grid-template-columns: 15rem minmax(0, 1fr);
  align-items: start;
  gap: var(--space-6);
}

.doc-overview {
  position: sticky;
  inset-block-start: calc(var(--header-height) + var(--space-4));
  display: grid;
  gap: var(--space-3);
}

.doc-fold {
  margin-block-end: var(--space-3);
  border: 1px solid var(--line);
  border-radius: var(--radius-card);
  background: var(--page);
}

.doc-fold > summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-1) var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-card);
  list-style: none;
  cursor: pointer;
}

.doc-fold > summary::-webkit-details-marker {
  display: none;
}

.doc-fold > summary:focus-visible {
  box-shadow: var(--focus);
  outline: none;
}

.doc-fold > summary h2 {
  margin: 0;
  font-size: 1.05rem;
}

.doc-fold > summary h2::before {
  content: "›";
  display: inline-block;
  inline-size: 1em;
  color: var(--text-faint);
  transition: transform var(--duration) var(--ease);
}

.doc-fold[open] > summary h2::before {
  transform: rotate(90deg);
}

.doc-todo {
  color: var(--text-muted);
  font-size: 0.85rem;
}

.doc-body {
  padding: 0 var(--space-4) var(--space-4);
  border-block-start: 1px solid var(--line);
}

.doc-about > summary {
  margin-block: var(--space-3);
  color: var(--text-faint);
  font-size: 0.82rem;
  cursor: pointer;
}

.doc-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
  gap: var(--space-4);
}

.hbars.page-bars li,
.hbars.coverage-bars li {
  grid-template-columns: minmax(6rem, 12rem) minmax(0, 1fr) 5rem;
}

.hbars.page-bars .hbar-label,
.hbars.coverage-bars .hbar-label {
  overflow: hidden;
  font-family: inherit;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.filter-chips {
  margin-block-end: var(--space-2);
}

.tone-documented { background: var(--documented); fill: var(--documented); }
.tone-inferred { background: var(--inferred); fill: var(--inferred); }
.tone-other { background: var(--series-other); fill: var(--series-other); }
.tone-quiet { background: var(--line-strong); fill: var(--line-strong); }

@media (max-width: 60rem) {
  .doc-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .doc-overview {
    position: static;
    grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  }
}
```

- [ ] **Step 6: Run the API tests and see them pass**

Run: `mise exec -- uv run pytest tests/api/test_documentation_page.py tests/api/test_shell.py -q`
Expected: PASS, the existing tests included (their strings are kept: "No pages yet", "1 of 3", "1 message withheld", "The commit history couldn't be read", "Trend over 1 update: 33%", "+17 points since the last update").

- [ ] **Step 7: Lint, format, type-check, and commit**

Run: `mise exec -- just format && mise exec -- just lint && mise exec -- just typecheck`

```bash
git add src/codetrail/web/templates/documentation.html src/codetrail/web/app.py src/codetrail/web/static/css/components.css tests/api/test_documentation_page.py
git commit  # feat(web): lay out the Documentation page as an overview beside folding sections
```

---

### Task 3: the filter chips, in the browser

**Files:**
- Create: `src/codetrail/web/static/js/documentation.js`
- Modify: `src/codetrail/web/static/js/main.js`
- Modify: `tests/browser/conftest.py` (a `settings` marker), `pyproject.toml` (register it)
- Create: `tests/browser/test_documentation_page.py`
- Modify: `tests/browser/test_accessibility.py`

**Interfaces:**
- Consumes: Task 2's markup (`[data-filterable]`, `[data-filter]`, `[data-show]`, `[data-filter-status]`, `li[data-kind]`, `[data-more][data-count]`, `<details class="doc-fold">`, `.doc-overview`).
- Produces: `setUpDocumentation()` exported from `documentation.js`.

The browser fixture's target has two commits, both subject only (no chips there), and two unmentioned facts, a project and a resource (chips there); with no ADRs and every share at 0%, Documented rationale opens.

- [ ] **Step 1: Let a browser test choose its settings** — in `tests/browser/conftest.py`, give `site` the `request` fixture and merge a `settings` marker's mapping:

```python
@pytest.fixture
def site(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[Site]:
    ...
    marker = request.node.get_closest_marker("settings")
    extra = marker.args[0] if marker else {}
    settings = GlobalConfig.model_validate({"server": {"port": port, "update_cooldown_seconds": 0}, **extra})
```

and in `pyproject.toml`'s `markers`:

```toml
    "settings(mapping): global settings merged into the browser test's site",
```

- [ ] **Step 2: Write the failing browser tests** in `tests/browser/test_documentation_page.py`

```python
"""The Documentation page's folding sections and filter chips (design section 21)."""

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.browser.conftest import Site

pytestmark = pytest.mark.browser


def fold(page: Page, name: str) -> Locator:
    return page.locator(f"#{name} > details.doc-fold")


def test_the_section_most_in_need_opens_and_the_others_on_demand(page: Page, site: Site, errors: list[str]) -> None:
    site.sign_in(page, "/documentation")
    expect(fold(page, "rationale")).to_have_attribute("open", "")  # every share is 0%: the first opens
    expect(fold(page, "commits")).not_to_have_attribute("open", "")
    fold(page, "commits").locator(":scope > summary").click()
    expect(fold(page, "commits")).to_have_attribute("open", "")
    summary = fold(page, "decisions").locator(":scope > summary")
    summary.focus()
    page.keyboard.press("Enter")
    expect(fold(page, "decisions")).to_have_attribute("open", "")
    assert errors == []


def test_a_chip_shows_one_kind_and_says_so(page: Page, site: Site) -> None:
    site.sign_in(page, "/documentation")
    fold(page, "coverage").locator(":scope > summary").click()
    chips = page.locator("#coverage [data-filter]")
    expect(chips).to_be_visible()
    chips.locator('[data-show="resource"]').click()
    expect(chips.locator('[data-show="resource"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#coverage li[data-kind="project"]')).to_be_hidden()
    expect(page.locator('#coverage li[data-kind="resource"]')).to_be_visible()
    expect(page.locator("#coverage [data-filter-status]")).to_have_text("Resources · 1")
    chips.locator('[data-show="all"]').click()
    expect(page.locator('#coverage li[data-kind="project"]')).to_be_visible()


@pytest.mark.settings({"metrics": {"max_listed": 1}})
def test_a_chip_filters_the_folded_rest_too(page: Page, site: Site) -> None:
    site.sign_in(page, "/documentation")
    fold(page, "coverage").locator(":scope > summary").click()
    rest = page.locator("#coverage details.more")
    folded = rest.locator("li[data-kind]").get_attribute("data-kind")
    shown = "project" if folded == "resource" else "resource"
    page.locator(f'#coverage [data-show="{shown}"]').click()
    expect(rest).to_be_hidden()  # nothing of that kind folded away: no "0 more"
    page.locator(f'#coverage [data-show="{folded}"]').click()
    expect(rest).to_be_visible()
    expect(rest.locator(f'[data-more="{folded}"]')).to_have_text("1 more")
    expect(rest.locator('[data-more="all"]')).to_be_hidden()


def test_the_overview_stays_in_view_then_becomes_a_row(page: Page, site: Site) -> None:
    page.set_viewport_size({"width": 1280, "height": 720})
    site.sign_in(page, "/documentation")
    overview = page.locator(".doc-overview")
    assert overview.evaluate("element => getComputedStyle(element).position") == "sticky"
    page.set_viewport_size({"width": 375, "height": 812})
    assert overview.evaluate("element => getComputedStyle(element).position") == "static"
    columns = overview.evaluate("element => getComputedStyle(element).gridTemplateColumns")
    assert len(columns.split()) >= 2  # the tiles side by side, as a row
```

and in `tests/browser/test_accessibility.py`:

```python
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_documentation_page_with_every_section_open(
    page: Page, site: Site, errors: list[str], scheme: str
) -> None:
    page.emulate_media(color_scheme=scheme)  # type: ignore[arg-type]
    site.sign_in(page, "/documentation")
    for summary in page.locator("details.doc-fold:not([open]) > summary").all():
        summary.click()
    page.evaluate(AXE)
    result = page.evaluate(
        "async () => (await axe.run(document, {resultTypes: ['violations']})).violations"
        ".filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id)"
    )
    assert result == []
    assert errors == []
```

- [ ] **Step 3: Run them and see them fail**

Run: `mise exec -- just test-browser -k "documentation"`
Expected: the chip tests FAIL (the chips stay hidden); the others may pass already, since the sections need no script.

- [ ] **Step 4: Write `src/codetrail/web/static/js/documentation.js`**

```js
// The Documentation page's filter chips (design section 21.2): each shows one kind of item, in its list and in the
// list's folded rest. The server writes every count and label; this sets only hidden, aria-pressed and the live
// region's words, copied from the chip. Without it, every item shows.
export function setUpDocumentation() {
  for (const area of document.querySelectorAll("[data-filterable]")) {
    const group = area.querySelector("[data-filter]");
    if (!group) continue;
    const status = area.querySelector("[data-filter-status]");
    const buttons = [...group.querySelectorAll("[data-show]")];
    group.hidden = false;
    for (const button of buttons) {
      button.addEventListener("click", () => {
        const kind = button.dataset.show;
        for (const other of buttons) other.setAttribute("aria-pressed", String(other === button));
        for (const item of area.querySelectorAll("li[data-kind]")) item.hidden = kind !== "all" && item.dataset.kind !== kind;
        for (const more of area.querySelectorAll("[data-more]")) {
          more.hidden = more.dataset.more !== kind;
          if (!more.hidden) more.closest("details").hidden = more.dataset.count === "0";
        }
        if (status) status.textContent = button.textContent;
      });
    }
  }
}
```

and in `main.js`, `import { setUpDocumentation } from "./documentation.js";` beside the other imports, and `setUpDocumentation();` before `document.documentElement.dataset.ready = "true";`.

- [ ] **Step 5: Run the browser tests and see them pass**

Run: `mise exec -- just test-browser -k "documentation or accessibility or keyboard"`
Expected: PASS.

- [ ] **Step 6: Lint and commit**

Run: `mise exec -- just lint`

```bash
git add src/codetrail/web/static/js/documentation.js src/codetrail/web/static/js/main.js tests/browser pyproject.toml
git commit  # feat(web): filter the Documentation page's long lists by kind
```

---

### Task 4: language

**Files:**
- Modify: `src/codetrail/locales/codetrail.pot`, `src/codetrail/locales/ar/LC_MESSAGES/codetrail.po` and `.mo` (through `just catalogs`)
- Modify: `tests/fixtures/locales/ar/LC_MESSAGES/codetrail.po`
- Modify: `tests/browser/test_languages.py`

- [ ] **Step 1: Write the failing test** in `tests/browser/test_languages.py`

```python
def test_the_documentation_page_follows_the_language(page: Page, site: Site, errors: list[str]) -> None:
    in_arabic(page, site, "/documentation")
    expect(page.locator("#rationale .doc-about > summary")).to_have_text("ما الذي يُحسب")
    bar = page.locator("#rationale .stack-bar")
    expect(bar).to_have_attribute("dir", "ltr")  # charts run left to right, like their numbers
    legend = page.locator("#rationale .stack-legend").text_content() or ""
    assert any(character.isascii() and character.isdigit() for character in legend)
    assert not any(0x0660 <= ord(character) <= 0x0669 for character in legend)  # no Arabic-Indic digits
    assert errors == []
```

The browser fixture's guide has pages, so Documented rationale draws its bar and legend.

- [ ] **Step 2: Run it and see it fail**

Run: `mise exec -- just test-browser -k "documentation_page_follows"`
Expected: FAIL: the summary reads "What this counts", since no catalog translates it yet.

- [ ] **Step 3: Translate.** Add to the test catalog, `tests/fixtures/locales/ar/LC_MESSAGES/codetrail.po`:

```po
msgid "What this counts"
msgstr "ما الذي يُحسب"
```

Run `mise exec -- just catalogs`, then fill each new entry in `src/codetrail/locales/ar/LC_MESSAGES/codetrail.po`, removing any `#, fuzzy` mark `pybabel update` added to them:

```po
msgid "What this counts"
msgstr "ما الذي يُحسب"

msgid "Overview"
msgstr "نظرة عامة"

msgid "Show"
msgstr "عرض"

msgid "All"
msgstr "الكل"

msgid "Body without a why"
msgstr "متن بلا سبب"

msgid "Subject only"
msgstr "عنوان فقط"

msgid "body without a why"
msgstr "متن بلا سبب"

msgid "subject only"
msgstr "عنوان فقط"

msgid "Packages"
msgstr "الحزم"

msgid "Projects"
msgstr "المشاريع"

msgid "Resources"
msgstr "الموارد"

msgid "ADRs"
msgstr "سجلات القرارات"

msgid "Documents"
msgstr "المستندات"

msgid "Code comments"
msgstr "تعليقات الشيفرة"

msgid "Commit messages"
msgstr "رسائل الإيداعات"

msgid "Explain why"
msgstr "تشرح السبب"

msgid "By page, most inferred first"
msgstr "حسب الصفحة، الأكثر استنتاجًا أولًا"

msgid "By status"
msgstr "حسب الحالة"

msgid "Needs attention"
msgstr "تحتاج إلى انتباه"

msgid "Worth a look"
msgstr "تستحق نظرة"

msgid "No pages yet"
msgstr "لا صفحات بعد"

msgid "Nothing inferred"
msgstr "لا شيء مُستنتَج"

msgid "None found"
msgstr "لم يُعثر على شيء"

msgid "Explained by a guide page"
msgstr "تشرحها صفحة من الدليل"

#, python-format
msgid "%(documented)s documented, %(inferred)s inferred"
msgstr "%(documented)s موثّقة، %(inferred)s مُستنتَجة"

msgid ""
"A proposed ADR needs attention once it waits longer than the target's "
"settings allow, and a superseded or deprecated one while a guide page still "
"cites it. Worth a look: proposed ADRs with no date, and accepted ones no page "
"cites."
msgstr ""
"يحتاج سجل القرار المقترح إلى انتباه إذا انتظر أطول مما تسمح به إعدادات "
"المستودع، وكذلك السجل المُستبدَل أو المُهمَل ما دامت صفحة من الدليل تستشهد به. "
"تستحق نظرة: السجلات المقترحة بلا تاريخ، والمقبولة التي لا تستشهد بها أي صفحة."

msgid ""
"A package counts when a document names it as a whole word, a project when "
"its folder holds a README or a document names its folder, and a resource "
"when a document names its address. A fact is explained when it falls in the "
"scope of a written area or concept page."
msgstr ""
"تُحسب الحزمة إذا ذكرها مستند بكلمة كاملة، والمشروع إذا احتوى مجلده على ملف "
"README أو ذكر مستندٌ مجلده، والمورد إذا ذكر مستندٌ عنوانه. وتُعدّ الحقيقة "
"مشروحة إذا وقعت في نطاق صفحة منطقة أو مفهوم مكتوبة."
```

`pybabel update` wraps the two long msgids its own way: match them by their text, not their line breaks. Then the plural entries, in the catalog's style (zero, one, two, few, many, other):

```po
msgid "%(count)s inferred, worth documenting"
msgid_plural "%(count)s inferred, worth documenting"
msgstr[0] "لا شيء مُستنتَج"
msgstr[1] "واحدة مُستنتَجة تستحق التوثيق"
msgstr[2] "اثنتان مُستنتَجتان تستحقان التوثيق"
msgstr[3] "%(count)s مُستنتَجة تستحق التوثيق"
msgstr[4] "%(count)s مُستنتَجة تستحق التوثيق"
msgstr[5] "%(count)s مُستنتَجة تستحق التوثيق"

msgid "%(count)s without a why, of the latest %(measured)s"
msgid_plural "%(count)s without a why, of the latest %(measured)s"
msgstr[0] "لا إيداع بلا سبب، من آخر %(measured)s"
msgstr[1] "إيداع واحد بلا سبب، من آخر %(measured)s"
msgstr[2] "إيداعان بلا سبب، من آخر %(measured)s"
msgstr[3] "%(count)s إيداعات بلا سبب، من آخر %(measured)s"
msgstr[4] "%(count)s إيداعًا بلا سبب، من آخر %(measured)s"
msgstr[5] "%(count)s إيداع بلا سبب، من آخر %(measured)s"

msgid "%(count)s needs attention"
msgid_plural "%(count)s need attention"
msgstr[0] "لا شيء يحتاج إلى انتباه"
msgstr[1] "واحد يحتاج إلى انتباه"
msgstr[2] "اثنان يحتاجان إلى انتباه"
msgstr[3] "%(count)s تحتاج إلى انتباه"
msgstr[4] "%(count)s تحتاج إلى انتباه"
msgstr[5] "%(count)s تحتاج إلى انتباه"

msgid "%(count)s to check"
msgid_plural "%(count)s to check"
msgstr[0] "لا شيء للتحقق"
msgstr[1] "واحد للتحقق"
msgstr[2] "اثنان للتحقق"
msgstr[3] "%(count)s للتحقق"
msgstr[4] "%(count)s للتحقق"
msgstr[5] "%(count)s للتحقق"

msgid "%(count)s never mentioned"
msgid_plural "%(count)s never mentioned"
msgstr[0] "لا شيء غير مذكور"
msgstr[1] "واحد غير مذكور"
msgstr[2] "اثنان غير مذكورين"
msgstr[3] "%(count)s غير مذكورة"
msgstr[4] "%(count)s غير مذكورة"
msgstr[5] "%(count)s غير مذكورة"

msgid "%(count)s unexplained"
msgid_plural "%(count)s unexplained"
msgstr[0] "لا شيء بلا شرح"
msgstr[1] "واحد بلا شرح"
msgstr[2] "اثنان بلا شرح"
msgstr[3] "%(count)s بلا شرح"
msgstr[4] "%(count)s بلا شرح"
msgstr[5] "%(count)s بلا شرح"

msgid "proposed %(count)s day ago"
msgid_plural "proposed %(count)s days ago"
msgstr[0] "مقترح اليوم"
msgstr[1] "مقترح منذ يوم واحد"
msgstr[2] "مقترح منذ يومين"
msgstr[3] "مقترح منذ %(count)s أيام"
msgstr[4] "مقترح منذ %(count)s يومًا"
msgstr[5] "مقترح منذ %(count)s يوم"
```

Then run `mise exec -- just catalogs` again to compile.

- [ ] **Step 4: Check every new string is translated, then run the tests**

Run: `mise exec -- uv run pybabel compile --statistics -d src/codetrail/locales -D codetrail` (expect no untranslated or fuzzy message), then `mise exec -- just test && mise exec -- just test-browser -k "language or documentation"`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codetrail/locales tests/fixtures/locales tests/browser/test_languages.py
git commit  # feat(web): translate the Documentation page's sections and charts
```

---

### Task 5: docs, the whole suite, review and finish

**Files:** `README.md` (current state), `docs/getting-started.md` (where it describes the Documentation page), `CHANGELOG.md` (Unreleased).

- [ ] **Step 1: Update the docs**
  - README's current state: Phase 18 is done; the Documentation page is an overview beside folding sections with charts.
  - getting-started: where it describes the Documentation page, say the overview shows each metric with its bar and trend, each section opens to a chart and its items, the section most in need opens first, and the long lists filter by kind.
  - CHANGELOG, under Unreleased: "The Documentation page shows an overview beside folding sections, each with a chart for its numbers; the section most in need opens first, and long lists filter by kind."
- [ ] **Step 2: Run everything**

Run: `mise exec -- just ci && mise exec -- just test-browser`
Expected: PASS.

- [ ] **Step 3: Commit** `docs(docs): describe the Documentation page's charts`.
- [ ] **Step 4: Review security** with the `security-reviewer` agent against `docs/security/review-checklist.md` (branch `feature/documentation-charts`, with the test results). Fix every critical and high finding in new commits and review again; fix medium ones or record why not.
- [ ] **Step 5: Finish into `develop`** (the founder pushes):

```bash
git flow feature finish documentation-charts --no-ff --no-push --keepremote --no-fetch -M "$(cat <<'EOF'
Merge branch 'feature/documentation-charts' into develop

Security review: <verdict> (reviewed <sha>)
- <each finding's one-line title>
EOF
)"
```

- [ ] **Step 6: Report** what landed on `develop` and what the founder should check: the page in light, dark, Arabic and at phone width, and the section that opens on their own repository.
