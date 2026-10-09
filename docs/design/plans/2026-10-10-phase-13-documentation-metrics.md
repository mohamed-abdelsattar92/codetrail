# Phase 13: documentation metrics implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show how well a target repository explains itself (the documented share of the guide's rationale, commits that explain why, ADR health, facts a document mentions or the guide explains), with the items behind each number and a trend per update, at no cost.

**Architecture:** A new `metrics` package computes each metric from the guide, the fact store, `source/` and the filtered history, as pure functions over already-read data plus one `build_report` that does the reading. An update writes each metric's numerator and denominator to a new `metric_values` table; the page, a home card and `codetrail metrics` show the live report next to that history.

**Tech Stack:** Python 3.14, the standard library's `sqlite3`, FastAPI with Jinja templates, `pathspec`, gitleaks through `SecretScanner`, pytest, Playwright for the browser tests. No new dependency.

**Spec:** [design section 18](../2026-10-05-codetrail-design.md#18-documentation-metrics), decision 13 in [brainstorm-decisions.md](../brainstorm-decisions.md).

## Global Constraints

- Nothing in this phase calls an assistant or spends anything.
- Only committed, allowed files are read: documents through the allowed-files reader over `source/`, within `extract.max_file_bytes`; commit messages after gitleaks, flagged ones withheld.
- Configuration, not constants: `[metrics] document_globs` and `commit_why_pattern` per target; `[metrics] commit_window = 200`, `proposed_adr_days = 30`, `trend_updates = 12`, `max_listed = 50` globally.
- No regular expression is built from a name a repository controls; names are matched by set lookup.
- Commit authors are never shown.
- Interface text goes through the gettext catalogs; the Arabic catalog gets every new string, and the compiled catalogs are committed (ADR 0013).
- Every commit: Conventional Commits with a scope, and the What, Why, Alternatives considered, Risks, Agent and model sections.

## Review Focus

1. **A guide page with odd front matter** (scope or facts not a list, a fact without an id): the page is counted for its rationale and skipped for coverage, and nothing raises. Test in Task 6.
2. **A repository with no commits besides merges, or a window larger than the history:** the commit metric shows 0 of 0 without dividing by zero, and its tile says there's nothing to measure. Tests in Tasks 4 and 9.
3. **gitleaks missing or failing while the page is shown:** the commit section says it isn't available and the other sections render. Test in Task 9.
4. **A binary or non-UTF-8 file matching the document globs** (an image under `docs/`): it's read with replacement characters and never raises. Test in Task 6.
5. **A first update after this ships, with no earlier rows:** the tiles show no change and the trend line has a single point, with its text alternative. Test in Task 9.

---

## File structure

| File | Responsibility |
|---|---|
| `src/codetrail/config.py` | `MetricsSettings` (global) and `TargetMetricsSettings` (per target) |
| `src/codetrail/repo/history.py` | `latest_commits`: the last N non-merge commits in one git call, withheld like the others |
| `src/codetrail/repo/source.py` | `allowed_reader`, moved from `update.system_reader`, so `metrics` can use it without importing `update` |
| `src/codetrail/generate/scope.py` | `entities_in_scope` made public (was `_in_scope`) |
| `src/codetrail/metrics/__init__.py` | Package docstring |
| `src/codetrail/metrics/rationale.py` | Documented share, by source type and by page; the inferred blocks |
| `src/codetrail/metrics/commits.py` | The three commit levels |
| `src/codetrail/metrics/decisions.py` | ADR health |
| `src/codetrail/metrics/coverage.py` | Mentioned in a document; explained by the guide |
| `src/codetrail/metrics/report.py` | `Metric`, `Value`, `MetricsReport`, `build_report` |
| `src/codetrail/metrics/history.py` | Writing and reading `metric_values` |
| `src/codetrail/database/migrations/0006_metric_values.sql` | The table |
| `src/codetrail/update.py` | Records the metrics at the end of an update |
| `src/codetrail/web/trend.py` | The trend line's SVG points and its text alternative |
| `src/codetrail/web/app.py` | `/documentation`, the report cache, the home card's data |
| `src/codetrail/web/templates/documentation.html` | The page |
| `src/codetrail/web/templates/home.html`, `layout/sidebar.html`, `layout/shortcuts.html`, `layout/icons.html` | Card, link, shortcut row, icon |
| `src/codetrail/web/static/js/shortcuts.js`, `static/css/components.css` | **G O**; tiles and trend line |
| `src/codetrail/cli.py` | `codetrail metrics <target>` |
| `src/codetrail/locales/…` | Catalog strings, Arabic translations, compiled catalogs |

---

### Task 1: settings

**Files:**
- Modify: `src/codetrail/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `GlobalConfig.metrics: MetricsSettings` with `commit_window: int`, `proposed_adr_days: int`, `trend_updates: int`, `max_listed: int`; `TargetConfig.metrics: TargetMetricsSettings` with `document_globs: list[str]`, `commit_why_pattern: str`, and `why: re.Pattern[str]` (compiled).

- [ ] **Step 1: Write the failing tests**

```python
def test_metrics_settings_have_their_defaults(tmp_path: Path) -> None:
    settings = GlobalConfig()
    assert (settings.metrics.commit_window, settings.metrics.proposed_adr_days) == (200, 30)
    assert (settings.metrics.trend_updates, settings.metrics.max_listed) == (12, 50)
    target = TargetConfig(repository=tmp_path, branch="develop")
    assert "docs/**" in target.metrics.document_globs
    assert target.metrics.why.search("Subject\n\nWhy: because")
    assert target.metrics.why.search("## Why\nbecause")
    assert not target.metrics.why.search("Nobody asked why.")


def test_an_invalid_why_pattern_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="commit_why_pattern"):
        TargetConfig(repository=tmp_path, branch="develop", metrics={"commit_why_pattern": "(unclosed"})
```

- [ ] **Step 2: Run them and see them fail** — `mise exec -- uv run pytest tests/unit/test_config.py -k metrics -q`: `AttributeError: metrics`.

- [ ] **Step 3: Implement**

```python
class MetricsSettings(Settings):
    commit_window: int = Field(default=200, gt=0, le=10_000)  # the latest non-merge commits read (design 18.1)
    proposed_adr_days: int = Field(default=30, ge=0)
    trend_updates: int = Field(default=12, ge=2, le=200)
    max_listed: int = Field(default=50, gt=0, le=5000)


class TargetMetricsSettings(Settings):
    document_globs: list[str] = ["README*", "*.md", "*.rst", "*.adoc", "*.txt", "docs/**"]  # gitignore syntax
    commit_why_pattern: str = r"(?im)^\s*(?:#+\s*)?why\b"

    @field_validator("commit_why_pattern")
    @classmethod
    def compiles(cls, pattern: str) -> str:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"isn't a valid regular expression: {error}") from error
        return pattern

    @property
    def why(self) -> re.Pattern[str]:
        return re.compile(self.commit_why_pattern)
```

Add `metrics: MetricsSettings = MetricsSettings()` to `GlobalConfig` and `metrics: TargetMetricsSettings = TargetMetricsSettings()` to `TargetConfig`.

- [ ] **Step 4: Run the tests and see them pass.**

---

### Task 2: the latest commits in one call, and the allowed-files reader

**Files:**
- Modify: `src/codetrail/repo/history.py`, `src/codetrail/repo/source.py`, `src/codetrail/update.py`, `src/codetrail/generate/scope.py` (rename `_in_scope` to `entities_in_scope`, update its two callers)
- Test: `tests/unit/test_history.py`, `tests/unit/test_source.py`

**Interfaces:**
- Produces: `latest_commits(mirror: Path, end: str, limit: int, scanner: SecretScanner) -> list[Commit]` (newest first, `files=[]`, `is_merge=False`); `allowed_reader(source: Path, files: Mapping[str, str], max_bytes: int) -> Callable[[str], bytes | None]` in `codetrail.repo.source`; `entities_in_scope(entities: list[Entity], entry: OutlineEntry) -> list[Entity]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_latest_commits_skip_merges_and_withhold_secrets(tmp_path: Path, scanner: SecretScanner) -> None:
    token = fake_github_token()
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    add_commit(checkout, {"b.md": "b\n"}, "feat: b\n\nWhy: because\nit helps.")
    add_commit(checkout, {"c.md": "c\n"}, f"fix: c\n\nThe old one was {token}")
    git(checkout, "checkout", "-q", "-b", "side")
    add_commit(checkout, {"d.md": "d\n"}, "feat: d")
    git(checkout, "checkout", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    mirror, end = mirror_of(checkout, tmp_path)
    commits = latest_commits(mirror, end, 10, scanner)
    assert [commit.subject for commit in commits] == [
        "feat: d", "[withheld: gitleaks flagged this message]", "feat: b", "Commit 0"]
    assert commits[2].body == "Why: because\nit helps."
    assert token not in repr(commits)
    assert len(latest_commits(mirror, end, 2, scanner)) == 2
```

`test_source.py`: move the existing tests of `system_reader` (if any; otherwise add) to `allowed_reader`: a path outside the list, a symlink, a file over the cap and a folder all read as `None`.

- [ ] **Step 2: Run and see them fail** (`ImportError`).

- [ ] **Step 3: Implement**

```python
LOG_FIELDS = 5  # sha, author, date, subject, body


def latest_commits(mirror: Path, end: str, limit: int, scanner: SecretScanner) -> list[Commit]:
    """The latest `limit` non-merge commits up to `end`, newest first, with flagged messages withheld.

    One git call: fields and records are separated by NUL, which no message can hold. Files aren't listed.
    """
    output = run_git(["log", f"-n{limit}", "--no-merges", "-z", "--format=%H%x00%an%x00%aI%x00%s%x00%b", end],
                     git_dir=mirror).decode("utf-8", "replace")  # fmt: skip
    fields = output.split("\0")
    commits = [
        Commit(sha, author, date, subject, body.strip(), [], False)
        for sha, author, date, subject, body in (fields[i : i + LOG_FIELDS] for i in
                                                  range(0, len(fields) - LOG_FIELDS + 1, LOG_FIELDS))
    ]  # fmt: skip
    return _withhold_flagged_messages(commits, scanner)
```

Move `system_reader` from `update.py` to `repo/source.py` as `allowed_reader`, unchanged, and import it in `update.py`.

- [ ] **Step 4: Run `just test-quick` and see everything pass.**
- [ ] **Step 5: Commit** — `refactor(repo): read the latest commits in one call, and share the allowed-files reader`.

---

### Task 3: documented share of rationale

**Files:**
- Create: `src/codetrail/metrics/__init__.py`, `src/codetrail/metrics/rationale.py`
- Test: `tests/unit/test_metrics_rationale.py`

**Interfaces:**
- Consumes: `parse_rationale` from `codetrail.generate.validate`; `Page` from `codetrail.guide`.
- Produces:

```python
SOURCE_KINDS = ("adr", "docs", "code", "commit")

@dataclass(frozen=True)
class InferredBlock:
    page: Page
    first_line: str

@dataclass(frozen=True)
class PageRationale:
    page: Page
    documented: int
    inferred: int

@dataclass(frozen=True)
class RationaleMetric:
    documented: int
    inferred: int
    by_source: dict[str, int]
    pages: list[PageRationale]          # most inferred first, then by title
    inferred_blocks: list[InferredBlock]

def guide_pages(pages: Iterable[Page]) -> list[Page]   # area and concept pages only
def citation_source(citation: str, adr: GitIgnoreSpec, documents: GitIgnoreSpec) -> str
def measure_rationale(pages: Iterable[Page], adr: GitIgnoreSpec, documents: GitIgnoreSpec) -> RationaleMetric
```

- [ ] **Step 1: Write the failing tests**

```python
ADR = GitIgnoreSpec.from_lines(["docs/adr/*.md"])
DOCUMENTS = GitIgnoreSpec.from_lines(["README*", "*.md", "docs/**"])

def page(page_id: str, kind: str, body: str) -> Page:
    return Page(page_id, {"kind": kind, "title": page_id}, body)

BODY = (
    '> [!documented] docs/adr/0001-x.md#L3-L3\n> "We chose Python."\n\n'
    '> [!documented] README.md#L1-L2\n> "A shop."\n\n'
    '> [!documented] app/db.py#L4\n> "Retries twice."\n\n'
    '> [!documented] commit:3f9c2e1\n> "Faster."\n\n'
    "> [!inferred]\n> It reads well.\n> Second line.\n"
)

def test_blocks_are_counted_by_source_and_page() -> None:
    metric = measure_rationale(
        [page("areas/app", "area", BODY), page("concepts/x", "concept", "> [!inferred]\n> Guess.\n"),
         page("digests/d", "digest", BODY), page("answers/a", "answer", BODY)], ADR, DOCUMENTS)
    assert (metric.documented, metric.inferred) == (4, 2)
    assert metric.by_source == {"adr": 1, "docs": 1, "code": 1, "commit": 1}
    assert [(row.page.id, row.documented, row.inferred) for row in metric.pages] == [
        ("areas/app", 4, 1), ("concepts/x", 0, 1)]  # equal inferred: by title
    assert [(block.page.id, block.first_line) for block in metric.inferred_blocks] == [
        ("areas/app", "It reads well."), ("concepts/x", "Guess.")]

def test_an_adr_that_also_matches_the_documents_is_an_adr() -> None:
    assert citation_source("docs/adr/0001-x.md#L3-L3", ADR, DOCUMENTS) == "adr"
    assert citation_source("docs/guide.md#L1", ADR, DOCUMENTS) == "docs"
    assert citation_source("src/app.py#L1", ADR, DOCUMENTS) == "code"
    assert citation_source("commit:abc1234", ADR, DOCUMENTS) == "commit"

def test_a_page_without_usable_front_matter_isnt_counted() -> None:
    assert measure_rationale([Page("areas/x", {}, BODY)], ADR, DOCUMENTS).documented == 0
```

- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement** with `parse_rationale(page.body)`; a block's first line is its first non-empty text line with the leading `> ` removed and quotation marks kept; `citation_source` strips `#…` before matching.
- [ ] **Step 4: Run and see them pass.**

---

### Task 4: commits that explain why

**Files:**
- Create: `src/codetrail/metrics/commits.py`
- Test: `tests/unit/test_metrics_commits.py`

**Interfaces:**
- Consumes: `Commit`, `WITHHELD_MESSAGE` from `codetrail.repo.history`.
- Produces:

```python
@dataclass(frozen=True)
class CommitMetric:
    explains_why: int
    body_only: int
    subject_only: int
    withheld: int
    without_why: list[Commit]   # newest first

    @property
    def measured(self) -> int:  # the share's denominator
        return self.explains_why + self.body_only + self.subject_only

def measure_commits(commits: Iterable[Commit], why: re.Pattern[str]) -> CommitMetric
```

- [ ] **Step 1: Write the failing tests** — four commits (why, body without why, subject only, withheld) give `(1, 1, 1, 1)`, `measured == 3`, and `without_why` is the body-only and subject-only commits in the order given; no commits give zeros.
- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Run and see them pass.**

---

### Task 5: ADR health

**Files:**
- Create: `src/codetrail/metrics/decisions.py`
- Test: `tests/unit/test_metrics_decisions.py`

**Interfaces:**
- Consumes: `Entity`; `FACT_LINK` and `parse_rationale` from `codetrail.generate.validate`; `guide_pages` from Task 3.
- Produces:

```python
@dataclass(frozen=True)
class DecisionMetric:
    by_status: dict[str, list[Entity]]   # "none" for no status
    overdue: list[Entity]
    undated: list[Entity]                # proposed, with no usable date
    superseded_cited: list[Entity]
    uncited: list[Entity]                # accepted, cited by no page

    @property
    def attention(self) -> int:
        return len(self.overdue) + len(self.superseded_cited)

def cited_decisions(pages: Iterable[Page], decisions: Iterable[Entity]) -> set[str]
def measure_decisions(decisions: Iterable[Entity], pages: Iterable[Page], today: date, proposed_days: int) -> DecisionMetric
```

- [ ] **Step 1: Write the failing tests**
  - proposed ADRs dated `today - 31` (overdue), `today - 30` (not), no date and `"2026-13-40"` (undated);
  - a superseded ADR cited by a documented block quoting its path, a deprecated one cited by `[[decision:ADR-0004]]`, a superseded one in a page's `facts`; one more superseded and uncited (not in the list);
  - an accepted ADR cited by nobody is in `uncited`; one cited only by a digest is still `uncited` (only area and concept pages cite);
  - `attention` adds overdue and superseded-cited.
- [ ] **Step 2–4:** fail, implement, pass.

---

### Task 6: mentioned in a document, and explained by the guide

**Files:**
- Create: `src/codetrail/metrics/coverage.py`
- Test: `tests/unit/test_metrics_coverage.py`

**Interfaces:**
- Consumes: `entities_in_scope` and `OutlineEntry` (Task 2).
- Produces:

```python
MENTIONABLE = (EntityKind.PACKAGE, EntityKind.PROJECT, EntityKind.RESOURCE)

def normalise(word: str) -> str            # lower-cased, runs of - _ . made one -, - and / trimmed
def words_of(text: str) -> set[str]        # every token, and each part of it split at /
def mention_name(entity: Entity) -> str    # package: its name; project: its folder; resource: its address

@dataclass(frozen=True)
class MentionMetric:
    mentioned: list[Entity]
    unmentioned: list[Entity]

def measure_mentions(entities: Iterable[Entity], documents: Mapping[str, str]) -> MentionMetric

def page_scope(page: Page) -> OutlineEntry | None   # None when its scope or facts aren't usable

@dataclass(frozen=True)
class ExplainedMetric:
    explained: int
    total: int
    unexplained: list[Entity]

def measure_explained(entities: list[Entity], pages: Iterable[Page]) -> ExplainedMetric
```

Token pattern: `[A-Za-z0-9@/._-]+`. A project counts when a document in its folder is named `README*` (case-insensitive; the root project's folder is `.`) or its folder is a word; never for the root project by name.

- [ ] **Step 1: Write the failing tests**
  - `package:pypi/fastapi` mentioned by "We use FastAPI." and by "fastapi/starlette"; not by "fastapix" or "fastapi-users";
  - `package:npm/@scope/ui-kit` mentioned by "`@scope/ui-kit`";
  - `package:pypi/pyyaml` mentioned by "PyYAML";
  - `resource:infra/aws_s3_bucket.assets` mentioned by "the `aws_s3_bucket.assets` bucket";
  - `project:services/api` mentioned by `services/api/README.md` existing, and by "see services/api/";
  - `project:.` mentioned by a root `README.md`;
  - a module is never in the lists;
  - a document holding `b"\xff\xfe"` decoded with replacement doesn't raise (the reader does the decoding in Task 7; here pass the replaced text);
  - `measure_explained`: a page with `scope: {paths: [app]}` explains `module:app/db.py`, a page naming `decision:ADR-0001` in `facts` explains it, `module:lib/x.py` is unexplained; a page with `scope: "app"` or `facts: [3]` is skipped without raising.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5: Commit Tasks 3–6** — `feat(metrics): measure rationale, commits, ADRs and coverage`.

---

### Task 7: the report, the table and the update

**Files:**
- Create: `src/codetrail/metrics/report.py`, `src/codetrail/metrics/history.py`, `src/codetrail/database/migrations/0006_metric_values.sql`
- Modify: `src/codetrail/update.py`
- Test: `tests/integration/test_metrics_report.py`, `tests/unit/test_metrics_history.py`, `tests/unit/test_database.py` (the new table exists)

**Interfaces:**
- Produces:

```python
class Metric(StrEnum):
    DOCUMENTED_SHARE = "documented_share"
    COMMIT_WHY_SHARE = "commit_why_share"
    ADR_ATTENTION = "adr_attention"
    MENTIONED_SHARE = "mentioned_share"
    EXPLAINED_SHARE = "explained_share"

@dataclass(frozen=True)
class Value:
    numerator: int
    denominator: int | None  # None for a count

    @property
    def share(self) -> float | None: ...   # None for a count or an empty denominator

@dataclass(frozen=True)
class MetricsReport:
    rationale: RationaleMetric
    commits: CommitMetric | None   # None when the history couldn't be read
    decisions: DecisionMetric
    mentions: MentionMetric
    explained: ExplainedMetric

    def values(self) -> dict[Metric, Value]: ...   # without COMMIT_WHY_SHARE when commits is None

def build_report(paths: Paths, name: str, settings: GlobalConfig, store: FactStore, today: date) -> MetricsReport

# history.py
def record_values(connection: sqlite3.Connection, snapshot_id: int, values: Mapping[Metric, Value]) -> None
def recent_values(connection: sqlite3.Connection, metric: Metric, limit: int) -> list[tuple[int, Value]]  # oldest first
def values_before(connection: sqlite3.Connection, snapshot_id: int) -> dict[Metric, Value]  # the latest earlier row of each
```

```sql
-- Each metric's value at each update (design section 18.3): numerator and denominator, the denominator empty for a count.
CREATE TABLE metric_values (
    snapshot INTEGER NOT NULL REFERENCES snapshots(id),
    metric TEXT NOT NULL,
    numerator INTEGER NOT NULL,
    denominator INTEGER,
    PRIMARY KEY (snapshot, metric)
);
```

`build_report` loads the target, the manifest (`source.json`), the guide's pages, `store.entities()`, the documents (allowed files matching `document_globs`, read with `allowed_reader` and decoded with replacement), and `latest_commits(mirror, manifest.commit, commit_window, SecretScanner(settings.tools))`; a `CodetrailError` from the history leaves `commits=None` and logs a warning.

`update.py`: `record_metrics(paths, name, settings, store)` builds the report for today and records its values at the latest snapshot; any exception is logged as a warning (`The documentation metrics weren't recorded: …`) and swallowed. It runs before the facts-only return, before the declined return and after the guide is written.

- [ ] **Step 1: Write the failing tests**
  - history: record two snapshots' values, `recent_values(…, limit=1)` is the newest, `values_before(second)` is the first's, recording a snapshot again replaces its rows;
  - integration: a fixture repository with a README naming one dependency, an ADR, two commits (one with `Why:`), updated with the fake assistant writing one page with a documented and an inferred block; `build_report` gives documented `1 of 2`, commits `1 of 3` (the fixture's first commit is `Commit 0` with no body), mentions and explained with the expected counts;
  - a facts-only update, a declined update and a full update each leave one row per metric at their snapshot; an update whose generation raises leaves none for its snapshot; `build_report` raising inside `record_metrics` (monkeypatched) logs a warning and the update returns normally.
- [ ] **Step 2–4:** fail, implement, pass. Run `just test-quick`.
- [ ] **Step 5: Commit** — `feat(metrics): build the report and record it at each update`.

---

### Task 8: the trend line

**Files:**
- Create: `src/codetrail/web/trend.py`
- Test: `tests/unit/test_trend.py`

**Interfaces:**
- Produces: `trend_points(values: Sequence[float], width: int = 120, height: int = 32) -> str` (SVG `points`, the oldest left, min and max scaled to the height with 2 px padding; one value draws a flat line across; none gives `""`).

- [ ] **Step 1: Failing tests:** `trend_points([0.5])` is a flat line `"2.0,16.0 118.0,16.0"`; `trend_points([0, 1])` goes from the bottom left to the top right; equal values are flat; only numbers appear in the output.
- [ ] **Step 2–4:** fail, implement, pass.

---

### Task 9: the Documentation page, the home card, the sidebar and **G O**

**Files:**
- Create: `src/codetrail/web/templates/documentation.html`
- Modify: `src/codetrail/web/app.py`, `templates/home.html`, `templates/layout/sidebar.html`, `templates/layout/shortcuts.html`, `templates/layout/icons.html`, `static/js/shortcuts.js`, `static/css/components.css`
- Test: `tests/api/test_documentation_page.py`

**Interfaces:**
- Consumes: `build_report`, `Metric`, `Value`, `recent_values`, `values_before`, `trend_points`.
- Produces: `GET /documentation`; the template context `report`, `values` (`dict[Metric, Value]`), `before` (`dict[Metric, Value]`), `trends` (`dict[Metric, list[float]]`), `listed` (`settings.metrics.max_listed`).

The report is cached in memory under a lock, keyed by `(latest snapshot id, guide head, guide has uncommitted changes, today)`. A `CodetrailError` or `OSError` while building it renders the page's "isn't available" state and logs why.

- [ ] **Step 1: Write the failing tests**
  - `/documentation` without the session is refused like every page (`test_security`'s helper);
  - after a facts-only update: the page renders; the documented tile says there are no pages yet and links to the update dialog's opener (`data-update-open`), not to a POST; ADR, commit and mention sections show their counts;
  - after a full update with the fake assistant: "1 of 2" for the documented share, the inferred block's first line linking to `/pages/areas/app`, the commit without a why listed with its short sha and subject, no author name anywhere;
  - a commit whose message holds a fake GitHub token: the token isn't on the page, the withheld text is;
  - a file excluded by the target's ignore rules, matching the document globs and naming a dependency: the dependency is still listed as unmentioned, and the file's text isn't on the page;
  - with `SecretScanner.scan_text` monkeypatched to raise `CodetrailError`: the commit section says it isn't available, the others render;
  - one recorded update: no change is shown and the trend line's text alternative names the single value; two updates: the change since the previous one is shown;
  - the home page shows the "Documentation" card once facts exist; the sidebar links to `/documentation`; the security headers are the same as on `/decisions`.
- [ ] **Step 2–4:** fail, implement, pass.
- [ ] **Step 5: Commit** — `feat(web): show the documentation metrics on their own page and the home card`.

---

### Task 10: `codetrail metrics`

**Files:**
- Modify: `src/codetrail/cli.py`
- Test: `tests/e2e/test_metrics_command.py`

- [ ] **Step 1: Failing tests:** after one facts-only update, `codetrail metrics shop` prints one line per metric with "n of m" (or the count) and exits 0; after a second update it prints the change ("+1 since the last update" for a count, "+12 points" for a share); with no snapshot yet it prints `No facts yet. Run: codetrail update shop --facts-only` and exits 1; the database's `metric_values` row count is unchanged by the command.
- [ ] **Step 2–4:** fail, implement (`show_metrics(paths, name)` under `target_in_use`, building the report and reading `values_before`), pass.
- [ ] **Step 5: Commit** — `feat(cli): print the documentation metrics with codetrail metrics`.

---

### Task 11: catalogs, browser tests and docs

**Files:**
- Modify: `src/codetrail/locales/codetrail.pot`, `src/codetrail/locales/ar/LC_MESSAGES/codetrail.po` and `.mo`; `tests/browser/test_accessibility.py`, `tests/browser/test_keyboard.py`; `README.md`, `CHANGELOG.md`, `docs/getting-started.md`, design section 2.2 (the `metrics` row also depends on `generate`)
- Test: `tests/unit/test_catalogs.py` (existing), browser tests

- [ ] **Step 1:** add `/documentation` to the accessibility scan's pages and a **G O** case to the keyboard test; run `just test-browser` and see them fail.
- [ ] **Step 2:** run `just catalogs`, translate the new Arabic entries (marked for the founder's review in the commit), run `just catalogs` again to compile; `just test` passes the catalog tests.
- [ ] **Step 3:** README: a "📊 Documentation metrics" row in "What you get" and the command in the commands list; CHANGELOG: a line under Unreleased; getting-started: one paragraph on the page and the command.
- [ ] **Step 4:** `just ci` passes.
- [ ] **Step 5: Commit** — `feat(web): translate and test the documentation page` and `docs(docs): describe the documentation metrics`.

---

### Task 12: security review and finish

- [ ] Run the `security-reviewer` agent on `feature/documentation-metrics` with the test results; fix every critical and high finding with new commits, and medium ones or record why not.
- [ ] Finish with `git flow feature finish documentation-metrics --no-ff --no-push --keepremote --no-fetch -M "…"`, the review's verdict in the message; report to the founder, who pushes.
