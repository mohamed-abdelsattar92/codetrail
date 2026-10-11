# Phase 14: repository statistics implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show what a target repository is (its history, releases, code size and facts) on a Repository page, a home card and `codetrail stats`, with commits, code lines, files and the test share recorded per update for the trend, at no cost.

**Architecture:** The mirror keeps the target's tags that point into the branch, reconciled at each refresh. New `metrics` files compute each group as pure functions over already-read data (`activity.py`, `releases.py`, `size.py`, `inventory.py`), and `repository.py` reads the target through `repo` and builds a `RepositoryReport`. Its values join the documentation metrics' row set in `metric_values`. The page reuses the report cache, the tiles and the trend lines of Phase 13.

**Tech Stack:** Python 3.14, `sqlite3`, FastAPI with Jinja templates, `pathspec`, gitleaks through `SecretScanner`, pytest, Playwright. No new dependency.

**Spec:** [design section 19](../2026-10-05-codetrail-design.md#19-repository-statistics), decision 14 in [brainstorm-decisions.md](../brainstorm-decisions.md).

## Global Constraints

- Nothing in this phase calls an assistant or spends anything.
- Only `repo` reads the mirror; the reconcile writes only the mirror's own `refs/tags/`, and the target is read only by `upload-pack` over `file://`.
- No author, tagger, contributor count or number per person is read or shown.
- Tag names and messages, and commit subjects, pass gitleaks before they are shown; a flagged tag shows `[withheld: gitleaks flagged this tag]` and no message.
- Files are read only through `allowed_reader`; languages are a dictionary lookup.
- Configuration, not constants: `[metrics] test_globs` and `commit_type_pattern` per target; `[metrics] activity_months = 24`, `history_limit = 1000000` and `languages` globally.
- Interface text goes through the gettext catalogs; the Arabic catalog gets every new string; compiled catalogs are committed (ADR 0013).
- Every commit: Conventional Commits with a scope, and the What, Why, Alternatives considered, Risks, Agent and model sections.

## Review Focus

1. **A target with no tags, or with only tags on other branches:** the Releases tile says there are none and the section renders. Tests in Tasks 2 and 9.
2. **A repository with a single commit** (no gap, one week, one month): activity shows one active week of one, no longest gap, and nothing divides by zero. Test in Task 3.
3. **A tag deleted or moved in the target between two refreshes:** the mirror follows it and the page never lists the old one. Test in Task 2.
4. **A history longer than `history_limit`:** the counts stay exact (from `rev-list --count`) and the timeline says it shows the latest commits. Test in Task 3.
5. **A first update after this ships, or a database from Phase 13 with documentation rows only:** the new tiles show no change and a one-point trend. Test in Task 9.

---

## File structure

| File | Responsibility |
|---|---|
| `src/codetrail/config.py` | New global `metrics` settings (`activity_months`, `history_limit`, `languages`) and target ones (`test_globs`, `commit_type_pattern`, `types`) |
| `src/codetrail/repo/mirror.py` | Tag reconcile inside `refresh_mirror` |
| `src/codetrail/repo/history.py` | `Tag`, `read_tags`, `commit_totals`, `commit_times` |
| `src/codetrail/facts/store.py` | `snapshots`, `entity_counts`, `entity_spans` |
| `src/codetrail/metrics/activity.py` | History and commit types |
| `src/codetrail/metrics/releases.py` | Releases |
| `src/codetrail/metrics/size.py` | Code size |
| `src/codetrail/metrics/inventory.py` | Facts by kind, arrivals and removals |
| `src/codetrail/metrics/repository.py` | `RepositoryReport`, `build_repository_report` |
| `src/codetrail/metrics/report.py` | Four more `Metric` names |
| `src/codetrail/update.py` | Records both reports' values |
| `src/codetrail/web/trend.py` | `bars`: the activity bars' rectangles |
| `src/codetrail/web/documentation.py` | New counts in `COUNTS`; `age` |
| `src/codetrail/web/app.py` | `/repository`, its cache, the home card's data |
| `src/codetrail/web/templates/repository.html` | The page |
| `home.html`, `layout/sidebar.html`, `layout/shortcuts.html`, `layout/icons.html`, `static/js/shortcuts.js`, `static/css/components.css` | Card, link, **G T**, icon, bars |
| `src/codetrail/cli.py` | `codetrail stats <target>` |
| `src/codetrail/locales/…` | Catalog strings and Arabic translations |

---

### Task 1: settings

**Files:**
- Modify: `src/codetrail/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `MetricsSettings.activity_months: int`, `.history_limit: int`, `.languages: dict[str, str]` (keys lower-cased); `TargetMetricsSettings.test_globs: list[str]`, `.commit_type_pattern: str`, `.types: re.Pattern[str]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_repository_statistics_settings_have_their_defaults(tmp_path: Path) -> None:
    settings = GlobalConfig()
    assert (settings.metrics.activity_months, settings.metrics.history_limit) == (24, 1_000_000)
    assert settings.metrics.languages[".py"] == "Python"
    assert settings.metrics.languages["dockerfile"] == "Dockerfile"
    target = TargetConfig(repository=tmp_path, branch="develop")
    assert "tests/**" in target.metrics.test_globs
    found = target.metrics.types.match("feat(web): add the page")
    assert found and (found["type"], found["scope"]) == ("feat", "web")
    assert target.metrics.types.match("fix!: drop it")
    assert not target.metrics.types.match("Add the page")


def test_a_type_pattern_needs_a_type_group(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="type"):
        TargetConfig(repository=tmp_path, branch="develop", metrics={"commit_type_pattern": "^(feat|fix):"})
    with pytest.raises(ValidationError, match="regular expression"):
        TargetConfig(repository=tmp_path, branch="develop", metrics={"commit_type_pattern": "("})


def test_language_names_are_lower_cased() -> None:
    assert GlobalConfig(metrics={"languages": {".PY": "Python"}}).metrics.languages == {".py": "Python"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `mise exec -- uv run pytest tests/unit/test_config.py -k "statistics or type_pattern or language_names" -q`
Expected: FAIL, `activity_months` is unknown.

- [ ] **Step 3: Implement**

In `MetricsSettings`:

```python
    activity_months: int = Field(default=24, gt=0, le=600)  # months the activity bars show
    history_limit: int = Field(default=1_000_000, gt=0, le=100_000_000)  # commits whose dates are read
    languages: dict[str, str] = DEFAULT_LANGUAGES  # a file name or suffix, lower-cased, and its language

    @field_validator("languages")
    @classmethod
    def lower_case_names(cls, languages: dict[str, str]) -> dict[str, str]:
        return {name.lower(): language for name, language in languages.items()}
```

`DEFAULT_LANGUAGES`, above it, maps the common suffixes: `.py` Python, `.pyi` Python, `.ts` `.tsx` `.mts` `.cts` TypeScript, `.js` `.jsx` `.mjs` `.cjs` JavaScript, `.astro` Astro, `.swift` Swift, `.kt` `.kts` Kotlin, `.java` Java, `.go` Go, `.rs` Rust, `.rb` Ruby, `.php` PHP, `.c` `.h` C, `.cc` `.cpp` `.hpp` C++, `.cs` C#, `.m` `.mm` Objective-C, `.scala` Scala, `.sh` `.bash` `.zsh` Shell, `.sql` SQL, `.html` HTML, `.css` `.scss` CSS, `.vue` Vue, `.svelte` Svelte, `.tf` Terraform, `.yaml` `.yml` YAML, `.json` JSON, `.toml` TOML, `.xml` XML, `.proto` Protocol Buffers, `.graphql` GraphQL, and the names `dockerfile` Dockerfile, `makefile` Makefile, `justfile` just.

In `TargetMetricsSettings`:

```python
    test_globs: list[str] = ["test/**", "tests/**", "**/test_*.py", "**/*_test.*", "**/*.test.*", "**/*.spec.*",
                             "**/__tests__/**", "**/Tests/**"]  # fmt: skip
    commit_type_pattern: str = r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]*)\))?!?:[ \t]"

    @field_validator("commit_type_pattern")
    @classmethod
    def has_a_type(cls, pattern: str) -> str:
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"isn't a valid regular expression: {error}") from error
        if "type" not in compiled.groupindex:
            raise ValueError("needs a (?P<type>...) group")
        return pattern

    @property
    def types(self) -> re.Pattern[str]:
        return re.compile(self.commit_type_pattern)
```

- [ ] **Step 4: Run the tests, then the whole config suite**

Run: `mise exec -- uv run pytest tests/unit/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit** `feat(config): add the repository statistics' settings`

---

### Task 2: tags in the mirror

**Files:**
- Modify: `src/codetrail/repo/mirror.py`, `src/codetrail/repo/history.py`
- Test: `tests/unit/test_mirror.py`, `tests/unit/test_history.py`

**Interfaces:**
- Produces: `refresh_mirror` keeps the branch's tags; `Tag(name: str, commit: str, date: int, message: str)`; `WITHHELD_TAG: str`; `read_tags(mirror: Path, end: str, scanner: SecretScanner) -> list[Tag]` (any order).

- [ ] **Step 1: Write the failing tests** (in `test_mirror.py`)

```python
def test_the_mirror_keeps_only_the_branchs_tags(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    git(checkout, "tag", "-a", "v1", "-m", "First release")
    git(checkout, "tag", "light")
    git(checkout, "checkout", "-q", "-b", "side")
    side = add_commit(checkout, {"b.txt": "b\n"}, "side work")
    git(checkout, "tag", "-a", "vside", "-m", "Side release")
    git(checkout, "checkout", "-q", "develop")
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    assert tags_in(mirror) == ["light", "v1"]
    with pytest.raises(CodetrailError):
        run_git(["cat-file", "-e", side], git_dir=mirror)


def test_the_mirror_follows_deleted_and_moved_tags(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    git(checkout, "tag", "-a", "v1", "-m", "First")
    git(checkout, "tag", "old")
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    head = add_commit(checkout, {"a.txt": "b\n"})
    git(checkout, "tag", "-d", "old")
    git(checkout, "tag", "-f", "-a", "v1", "-m", "Moved")
    refresh_mirror(mirror, checkout, "develop")
    assert tags_in(mirror) == ["v1"]
    assert run_git(["rev-parse", "v1^{commit}"], git_dir=mirror).decode().strip() == head


def test_fetching_tags_leaves_the_target_untouched(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    git(checkout, "tag", "-a", "v1", "-m", "First")
    before = snapshot_tree(checkout)
    refresh_mirror(tmp_path / "mirror.git", checkout, "develop")
    refresh_mirror(tmp_path / "mirror.git", checkout, "develop")
    assert snapshot_tree(checkout) == before
```

with `tags_in(mirror)` returning `sorted(run_git(["for-each-ref", "--format=%(refname:strip=2)", "refs/tags"], git_dir=mirror).decode().split())`.

And in `test_history.py`:

```python
def test_tags_are_read_with_their_messages_and_dates(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    first = git(checkout, "rev-parse", "HEAD")
    git(checkout, "tag", "light")
    second = add_commit(checkout, {"a.txt": "b\n"})
    git(checkout, "tag", "-a", "v1", "-m", "Release one\n\nThe notes.")
    git(checkout, "tag", "-a", "secret", "-m", f"token {fake_github_token()}")
    blob = git(checkout, "rev-parse", "HEAD:a.txt")
    git(checkout, "tag", "on-a-blob", blob)
    mirror, end = mirror_of(checkout, tmp_path)
    tags = {tag.name: tag for tag in read_tags(mirror, end, scanner)}
    assert set(tags) == {"light", "v1", WITHHELD_TAG}
    assert (tags["light"].commit, tags["light"].message) == (first, "")
    assert (tags["v1"].commit, tags["v1"].message) == (second, "Release one\n\nThe notes.")
    assert tags["v1"].date == 1_790_000_000 + 60  # the tagger's date: `git` in the fixtures dates every call
    assert tags[WITHHELD_TAG].message == ""
```

(If the fixture's `git()` stamps the tag with date 0, assert `tags["v1"].date` equals the stamp the call used; read it with `git(checkout, "for-each-ref", "--format=%(creatordate:unix)", "refs/tags/v1")` instead of hard-coding it.)

- [ ] **Step 2: Run them to see them fail**

Run: `mise exec -- uv run pytest tests/unit/test_mirror.py tests/unit/test_history.py -k tag -q`
Expected: FAIL: no tags in the mirror; `read_tags` can't be imported.

- [ ] **Step 3: Implement the reconcile** in `mirror.py`:

```python
def refresh_mirror(mirror: Path, repository: Path, branch: str) -> str:
    """Clones or fetches the branch into the mirror, with the tags that point into it, and returns its head commit.

    Tags: the target's are listed, the mirror's that are gone or moved are deleted, and the branch's fetch brings the
    rest by git's tag auto-follow, which takes only tags on objects the mirror holds (design section 19.2).
    """
    check_branch(repository, branch)
    url = repository_url(repository)
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        run_git([... the same clone, still with --no-tags ...])
    _drop_stale_tags(mirror, url)
    run_git(["fetch", "--quiet", "--prune", "--", url, f"+refs/heads/{branch}:refs/heads/{branch}"], git_dir=mirror)
    return head_commit(mirror, branch)


def _drop_stale_tags(mirror: Path, url: str) -> None:
    """Deletes the mirror's tags that the target no longer has or that point elsewhere now; only refs/tags/."""
    listed = run_git(["ls-remote", "--tags", "--", url]).decode("utf-8", "surrogateescape")
    remote = {}
    for line in listed.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            remote[ref] = sha
    local = run_git(["for-each-ref", "--format=%(refname)%00%(objectname)", "refs/tags"], git_dir=mirror)
    stale = []
    for line in local.decode("utf-8", "surrogateescape").splitlines():
        ref, _, sha = line.partition("\0")
        if ref.startswith("refs/tags/") and remote.get(ref) != sha:
            stale.append(f"delete {ref}\0{sha}\0")
    if stale:
        run_git(["update-ref", "-z", "--stdin"], git_dir=mirror, input="".join(stale).encode("utf-8", "surrogateescape"))
```

- [ ] **Step 4: Implement `read_tags`** in `history.py`:

```python
WITHHELD_TAG = "[withheld: gitleaks flagged this tag]"
TAG_FIELDS = 8  # name, type, object, peeled type, peeled object, date, subject, body


@dataclass(frozen=True)
class Tag:
    name: str
    commit: str
    date: int  # seconds since the epoch: the tagger's date, or the commit's for a lightweight tag
    message: str  # an annotated tag's subject and body; empty for a lightweight or withheld tag


def read_tags(mirror: Path, end: str, scanner: SecretScanner) -> list[Tag]:
    """The tags on commits in `end`'s history, with flagged names and messages withheld; the tagger is never read.

    One git call, fields separated by NUL, which no name or message can hold. Tags on anything but a commit are
    skipped, so a tag on a blob of an excluded file is never read.
    """
    output = run_git(["for-each-ref", "--merged", end, "--format=%(refname:strip=2)%00%(objecttype)%00%(objectname)"
                      "%00%(*objecttype)%00%(*objectname)%00%(creatordate:unix)%00%(contents:subject)%00%(contents:body)%00",
                      "refs/tags"],
                     git_dir=mirror).decode("utf-8", "replace")  # fmt: skip
    fields = output.split("\0")
    tags = []
    for start in range(0, len(fields) - TAG_FIELDS + 1, TAG_FIELDS):
        name, kind, sha, peeled_kind, peeled, date, subject, body = fields[start : start + TAG_FIELDS]
        message = f"{subject}\n\n{body.strip()}" if body.strip() else subject
        name = name.lstrip("\n")  # each record ends in a newline after its last NUL; names can't hold one
        if kind == "commit":
            tags.append(Tag(name, sha, int(date), ""))
        elif kind == "tag" and peeled_kind == "commit":
            tags.append(Tag(name, peeled, int(date), message.strip()))  # the signature, if any, isn't in either part
    flagged = _flagged_indexes([f"{tag.name}\n{tag.message}" for tag in tags], scanner)
    return [Tag(WITHHELD_TAG, t.commit, t.date, "") if index in flagged else t for index, t in enumerate(tags)]
```

The subject and body are read apart because `%(contents)` of a signed tag would end with its signature.

- [ ] **Step 5: Run the tests, then the repo suites**

Run: `mise exec -- uv run pytest tests/unit/test_mirror.py tests/unit/test_history.py tests/unit/test_source.py -q`
Expected: PASS

- [ ] **Step 6: Commit** `feat(repo): keep the branch's tags in the mirror, and read them with their messages scanned`

---

### Task 3: history and commit types

**Files:**
- Modify: `src/codetrail/repo/history.py`
- Create: `src/codetrail/metrics/activity.py`
- Test: `tests/unit/test_history.py`, `tests/unit/test_metrics_activity.py`

**Interfaces:**
- Produces: `commit_totals(mirror: Path, end: str) -> tuple[int, int]` (all, merges); `commit_times(mirror: Path, end: str, limit: int) -> list[int]` (author times, newest first); `Activity`, `measure_activity(times: Sequence[int], commits: int, merges: int, today: date, months: int) -> Activity`; `CommitTypes`, `measure_types(commits: Iterable[Commit], pattern: re.Pattern[str], max_chars: int) -> CommitTypes`.

- [ ] **Step 1: Write the failing tests**

`test_history.py`:

```python
def test_commit_totals_and_times_read_no_text(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a": "1"}, {"a": "2"}])
    git(checkout, "checkout", "-q", "-b", "side")
    add_commit(checkout, {"b": "1"})
    git(checkout, "checkout", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "side", "-m", "Merge side", date=5)
    mirror, end = mirror_of(checkout, tmp_path)
    assert commit_totals(mirror, end) == (4, 1)
    times = commit_times(mirror, end, 10)
    assert len(times) == 4 and times == sorted(times, reverse=True)
    assert len(commit_times(mirror, end, 2)) == 2
```

`test_metrics_activity.py`:

```python
DAY = 86_400
START = int(datetime(2025, 1, 6, tzinfo=UTC).timestamp())  # a Monday


def test_activity_counts_months_weeks_and_the_longest_gap() -> None:
    times = [START, START + DAY, START + 15 * DAY, START + 70 * DAY]
    activity = measure_activity(sorted(times, reverse=True), 5, 1, date(2025, 4, 30), months=4)
    assert (activity.first, activity.latest) == (date(2025, 1, 6), date(2025, 3, 17))
    assert activity.months == [("2025-01", 3), ("2025-02", 0), ("2025-03", 1), ("2025-04", 0)]
    assert activity.years == [(2025, 4)]
    assert (activity.active_weeks, activity.weeks) == (3, 11)
    assert activity.longest_gap == (date(2025, 1, 21), date(2025, 3, 17))
    assert (activity.commits, activity.merges, activity.cut) == (5, 1, True)


def test_one_commit_has_no_gap() -> None:
    activity = measure_activity([START], 1, 0, date(2025, 1, 6), months=2)
    assert (activity.active_weeks, activity.weeks, activity.longest_gap) == (1, 1, None)
    assert activity.months == [("2024-12", 0), ("2025-01", 1)]


def test_no_commits_measure_nothing() -> None:
    activity = measure_activity([], 0, 0, date(2025, 1, 6), months=1)
    assert (activity.first, activity.weeks, activity.months) == (None, 0, [("2025-01", 0)])


def commit(subject: str) -> Commit:
    return Commit("a" * 40, "", "", subject, "", [], False)


def test_commit_types_count_types_and_scopes() -> None:
    subjects = ["feat(web): a", "Feat(web): b", "fix: c", "docs(docs)!: d", "Add e", WITHHELD_MESSAGE]
    found = measure_types([commit(s) for s in subjects], TargetMetricsSettings().types, 20_000)
    assert (found.matched, found.measured, found.used) == (4, 5, True)
    assert found.types == [("feat", 2), ("docs", 1), ("fix", 1)]
    assert found.scopes == [("web", 2), ("docs", 1)]


def test_commit_types_need_half_the_subjects() -> None:
    found = measure_types([commit(s) for s in ["feat: a", "Add b", "Add c"]], TargetMetricsSettings().types, 100)
    assert (found.matched, found.used) == (1, False)
```

- [ ] **Step 2: Run them to see them fail**

Run: `mise exec -- uv run pytest tests/unit/test_history.py tests/unit/test_metrics_activity.py -q`
Expected: FAIL on imports.

- [ ] **Step 3: Implement the history reads** (`history.py`):

```python
def commit_totals(mirror: Path, end: str) -> tuple[int, int]:
    """How many commits `end`'s history holds, and how many of them are merges."""
    total = run_git(["rev-list", "--count", "--end-of-options", end], git_dir=mirror)
    merges = run_git(["rev-list", "--count", "--merges", "--end-of-options", end], git_dir=mirror)
    return int(total), int(merges)


def commit_times(mirror: Path, end: str, limit: int) -> list[int]:
    """The author times of the latest `limit` commits, newest first: no message, name or email is read."""
    output = run_git(["log", f"-n{limit}", "--format=%at", "--end-of-options", end], git_dir=mirror)
    return [int(line) for line in output.split()]
```

- [ ] **Step 4: Implement `activity.py`**:

```python
"""The branch's history and its commit types (design section 19.1): dates and counts only, never people."""

@dataclass(frozen=True)
class Activity:
    commits: int
    merges: int
    first: date | None
    latest: date | None
    months: list[tuple[str, int]]  # "YYYY-MM" and its commits, the last `months` months to today, oldest first
    years: list[tuple[int, int]]  # each year from the first commit's to the latest's, oldest first
    active_weeks: int
    weeks: int  # weeks from the first commit's to the latest's, both counted
    longest_gap: tuple[date, date] | None
    cut: bool  # the history is longer than the times read


def measure_activity(times: Sequence[int], commits: int, merges: int, today: date, months: int) -> Activity:
    days = sorted(datetime.fromtimestamp(time, UTC).date() for time in times)
    month_counts = Counter(day.strftime("%Y-%m") for day in days)
    shown = []
    year, month = today.year, today.month
    for _ in range(months):
        shown.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    month_list = [(key, month_counts.get(key, 0)) for key in reversed(shown)]
    if not days:
        return Activity(commits, merges, None, None, month_list, [], 0, 0, None, len(times) < commits)
    first, latest = days[0], days[-1]
    year_counts = Counter(day.year for day in days)
    years = [(year, year_counts.get(year, 0)) for year in range(first.year, latest.year + 1)]
    active = len({_monday(day) for day in days})
    weeks = (_monday(latest) - _monday(first)).days // 7 + 1
    gaps = [(later - earlier, earlier, later) for earlier, later in pairwise(days)]
    widest = max(gaps, default=None)
    gap = (widest[1], widest[2]) if widest and widest[0].days > 0 else None
    return Activity(commits, merges, first, latest, month_list, years, active, weeks, gap, len(times) < commits)
```

with `_monday(day)` returning `day - timedelta(days=day.weekday())`.

`CommitTypes(matched: int, measured: int, types: list[tuple[str, int]], scopes: list[tuple[str, int]])` with `used` true when `measured and matched * 2 >= measured`; `measure_types` skips `WITHHELD_MESSAGE` subjects, matches `pattern.match(subject[:max_chars])`, lower-cases the type, counts non-empty scopes, and sorts both lists by count descending, then name.

- [ ] **Step 5: Run the tests**

Run: `mise exec -- uv run pytest tests/unit/test_history.py tests/unit/test_metrics_activity.py -q`
Expected: PASS

- [ ] **Step 6: Commit** `feat(metrics): measure the branch's history and commit types`

---

### Task 4: releases, code size and facts

**Files:**
- Create: `src/codetrail/metrics/releases.py`, `src/codetrail/metrics/size.py`, `src/codetrail/metrics/inventory.py`
- Modify: `src/codetrail/facts/store.py`
- Test: `tests/unit/test_metrics_releases.py`, `tests/unit/test_metrics_size.py`, `tests/unit/test_metrics_inventory.py`

**Interfaces:**
- Consumes: `Tag` (Task 2); `FactStore`.
- Produces:
  - `Releases(tags: list[Tag], since_latest: int | None, median_days: int | None)`; `measure_releases(tags: Sequence[Tag], since_latest: int | None) -> Releases` (tags newest first).
  - `LanguageSize(language: str, files: int, lines: int)`, `FileSize(path: str, language: str, lines: int)`, `CodeSize(files, languages, documents, other_files, binary_files, too_large_files, code_lines, test_lines, largest)` with `documents: LanguageSize`; `measure_size(paths: Iterable[str], read: Callable[[str], bytes | None], languages: Mapping[str, str], documents: GitIgnoreSpec, tests: GitIgnoreSpec) -> CodeSize`; `language_of(path: str, languages: Mapping[str, str]) -> str | None`; `count_lines(content: bytes) -> int`.
  - `FactStore.snapshots(limit: int) -> list[Snapshot]` (the latest, oldest first), `FactStore.entity_counts(snapshot_ids: Sequence[int]) -> dict[int, dict[str, int]]`, `FactStore.entity_spans(kind: EntityKind) -> list[tuple[str, int, int | None]]` (id, first snapshot, last snapshot or None while current).
  - `KindCount(kind: str, count: int, change: int | None, trend: list[int])`, `Change(package: str, snapshot: Snapshot)`, `Inventory(since: Snapshot | None, kinds: list[KindCount], arrived: list[Change], left: list[Change])`; `measure_inventory(store: FactStore, trend_updates: int) -> Inventory`.

- [ ] **Step 1: Write the failing tests**

Releases:

```python
def test_releases_are_newest_first_with_the_median_gap() -> None:
    tags = [Tag("v1", "a", 0, ""), Tag("v3", "c", 30 * DAY, "notes"), Tag("v2", "b", 10 * DAY, "")]
    releases = measure_releases(tags, since_latest=4)
    assert [tag.name for tag in releases.tags] == ["v3", "v2", "v1"]
    assert (releases.since_latest, releases.median_days) == (4, 15)


def test_no_release_has_no_median() -> None:
    assert measure_releases([], None) == Releases([], None, None)
    assert measure_releases([Tag("v1", "a", 0, "")], 0).median_days is None
```

Size:

```python
LANGUAGES = {".py": "Python", ".ts": "TypeScript", "dockerfile": "Dockerfile"}


def test_files_are_sorted_into_languages_documents_and_the_rest() -> None:
    files = {
        "app/main.py": b"a\nb\nc",          # 3 lines: the last has no newline
        "tests/test_main.py": b"a\n",
        "web/x.ts": b"",                     # a file with no lines
        "Dockerfile": b"FROM x\n",
        "README.md": b"# Hi\n\nText\n",
        "docs/notes.py": b"x\n",             # a document, whatever its suffix
        "logo.png": b"\x89PNG\0\0",
        "data.csv": b"a,b\n",
        "big.py": None,                      # over extract.max_file_bytes
    }
    size = measure_size(files, files.get, LANGUAGES, spec(["README*", "docs/**"]), spec(["tests/**"]))
    assert size.files == 9
    assert [(item.language, item.files, item.lines) for item in size.languages] == [
        ("Python", 2, 4), ("Dockerfile", 1, 1), ("TypeScript", 1, 0)]
    assert (size.documents.files, size.documents.lines) == (2, 4)
    assert (size.other_files, size.binary_files, size.too_large_files) == (1, 1, 1)
    assert (size.code_lines, size.test_lines) == (5, 1)
    assert [item.path for item in size.largest] == ["app/main.py", "Dockerfile", "tests/test_main.py", "web/x.ts"]
```

with `spec = GitIgnoreSpec.from_lines`. Also `count_lines(b"") == 0`, `count_lines(b"a") == 1`, `count_lines(b"a\n\n") == 2`.

Inventory:

```python
def test_inventory_counts_kinds_per_snapshot_and_package_changes(tmp_path: Path) -> None:
    store = FactStore(connect(tmp_path / "db"))
    first = store.record("c1", [package("httpx"), package("fastapi")], [])
    second = store.record("c2", [package("httpx"), package("pydantic")], [])
    inventory = measure_inventory(store, trend_updates=12)
    assert inventory.since == first
    assert inventory.kinds == [KindCount("package", 2, 0, [2, 2])]
    assert inventory.arrived == [Change("package:pydantic", second)]
    assert inventory.left == [Change("package:fastapi", second)]
```

(`package(name)` builds `Entity(f"package:{name}", EntityKind.PACKAGE)`; adapt `store.record`'s call to its real signature in `facts/store.py`.)

- [ ] **Step 2: Run them to see them fail**

Run: `mise exec -- uv run pytest tests/unit/test_metrics_releases.py tests/unit/test_metrics_size.py tests/unit/test_metrics_inventory.py -q`
Expected: FAIL on imports.

- [ ] **Step 3: Implement `releases.py`**: sort by `(date, name)` descending; `median_days` is `round(statistics.median(gaps) / 86_400)` over consecutive dates, or None with fewer than two tags.

- [ ] **Step 4: Implement `size.py`**: `count_lines` counts `b"\n"` and adds one when the content doesn't end in one; `language_of` looks up the lower-cased file name, then its lower-cased suffix (`PurePosixPath(name).suffix`); `measure_size` reads each path once, in this order: `None` is too large, a NUL byte is binary, a document match counts as a document, a language counts as code (and as a test when the test globs match), anything else is other. `languages` is sorted by lines then files, descending, then name; `largest` holds the code files sorted by lines descending, then path.

- [ ] **Step 5: Implement the store queries and `inventory.py`**:

```python
    def snapshots(self, limit: int) -> list[Snapshot]:
        rows = self.connection.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Snapshot(row["id"], row["commit_sha"], row["taken_at"]) for row in reversed(rows)]

    def entity_counts(self, snapshot_ids: Sequence[int]) -> dict[int, dict[str, int]]:
        """Each snapshot's entities by kind, from the validity ranges."""
        counts: dict[int, dict[str, int]] = {snapshot_id: {} for snapshot_id in snapshot_ids}
        marks = ", ".join("?" for _ in snapshot_ids)
        rows = self.connection.execute(
            f"SELECT s.id AS snapshot, e.kind AS kind, COUNT(*) AS count FROM snapshots s JOIN entities e"
            f" ON e.first_seen <= s.id AND (e.last_seen IS NULL OR e.last_seen >= s.id)"
            f" WHERE s.id IN ({marks}) GROUP BY s.id, e.kind", tuple(snapshot_ids))  # fmt: skip
        for row in rows:
            counts[row["snapshot"]][row["kind"]] = row["count"]
        return counts

    def entity_spans(self, kind: EntityKind) -> list[tuple[str, int, int | None]]:
        """Each entity of the kind ever seen: its first snapshot, and its last unless it is still current."""
        rows = self.connection.execute(
            "SELECT id, MIN(first_seen) AS first, CASE WHEN SUM(last_seen IS NULL) > 0 THEN NULL"
            " ELSE MAX(last_seen) END AS last FROM entities WHERE kind = ? GROUP BY id ORDER BY id", (str(kind),))
        return [(row["id"], row["first"], row["last"]) for row in rows]
```

`measure_inventory` takes `store.snapshots(trend_updates)`, counts them, and builds one `KindCount` per kind current in the latest snapshot (sorted by count descending, then kind), its `change` the difference with the previous snapshot's count (None with one snapshot) and its `trend` the counts oldest first. `since` is the earliest snapshot overall (`SELECT … ORDER BY id LIMIT 1`, a `FactStore.earliest_snapshot()`). Arrivals are packages whose first snapshot is after `since`, with that snapshot; removals are packages with a last snapshot, with the first snapshot after it; both newest first.

- [ ] **Step 6: Run the tests**

Run: `mise exec -- uv run pytest tests/unit/test_metrics_releases.py tests/unit/test_metrics_size.py tests/unit/test_metrics_inventory.py tests/unit/test_fact_store.py -q`
Expected: PASS

- [ ] **Step 7: Commit** `feat(metrics): measure releases, code size and the facts by kind`

---

### Task 5: the report and its recording

**Files:**
- Create: `src/codetrail/metrics/repository.py`
- Modify: `src/codetrail/metrics/report.py`, `src/codetrail/update.py`, `src/codetrail/web/documentation.py`
- Test: `tests/integration/test_repository_report.py`, `tests/integration/test_metrics_report.py`

**Interfaces:**
- Consumes: Tasks 1 to 4.
- Produces: `Metric.COMMITS`, `Metric.CODE_LINES`, `Metric.SOURCE_FILES`, `Metric.TEST_SHARE`; `RepositoryReport(activity: Activity | None, types: CommitTypes | None, releases: Releases | None, size: CodeSize, inventory: Inventory)` with `values() -> dict[Metric, Value]`; `build_repository_report(paths: Paths, name: str, settings: GlobalConfig, store: FactStore, today: date) -> RepositoryReport`; `record_metrics` writes both reports' values.

- [ ] **Step 1: Write the failing tests** (`test_repository_report.py`, with the `paths` fixture of `test_metrics_report.py` plus a tag):

```python
def test_the_repository_report_reads_history_tags_files_and_facts(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    with store_of(paths) as store:
        report = build_repository_report(paths, "shop", GlobalConfig(), store, date(2026, 10, 10))
    assert report.activity and (report.activity.commits, report.activity.merges) == (3, 0)
    assert report.types and report.types.types == [("feat", 1), ("fix", 1)]
    assert report.releases and [tag.name for tag in report.releases.tags] == ["v1"]
    assert report.releases.since_latest == 2
    assert {item.language for item in report.size.languages} == {"Python", "TOML"}
    assert report.values()[Metric.COMMITS] == Value(3, None)
    assert report.values()[Metric.TEST_SHARE].denominator == report.size.code_lines


def test_an_update_records_both_reports(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    recorded = next(iter(rows(paths).values()))
    assert {"documented_share", "commits", "code_lines", "source_files", "test_share"} <= set(recorded)


def test_a_failing_repository_report_still_records_the_documentation_metrics(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("codetrail.update.build_repository_report", broken)
    run_update(paths, "shop", facts_only=True)
    recorded = next(iter(rows(paths).values()))
    assert "documented_share" in recorded and "commits" not in recorded
    assert "repository statistics weren't recorded" in caplog.text
```

(The fixture tags the first commit `v1` in the checkout before `write_target`; `run_update`'s signature and the `rows`/`store_of` helpers follow `test_metrics_report.py`.)

- [ ] **Step 2: Run them to see them fail**

Run: `mise exec -- uv run pytest tests/integration/test_repository_report.py -q`
Expected: FAIL on imports.

- [ ] **Step 3: Implement** `Metric`'s new members in `report.py`; `repository.py`:

```python
def build_repository_report(paths: Paths, name: str, settings: GlobalConfig, store: FactStore, today: date) -> RepositoryReport:
    target = load_target(paths, name)
    data = paths.target_data(name)
    manifest = SourceManifest.load(data / "source.json")
    files = manifest.files if manifest else {}
    read = allowed_reader(data / "source", files, settings.extract.max_file_bytes)
    size = measure_size(files, read, settings.metrics.languages, GitIgnoreSpec.from_lines(target.metrics.document_globs),
                        GitIgnoreSpec.from_lines(target.metrics.test_globs))  # fmt: skip
    activity = types = releases = None
    if manifest is not None:
        mirror, end = data / "mirror.git", manifest.commit
        try:
            commits, merges = commit_totals(mirror, end)
            times = commit_times(mirror, end, settings.metrics.history_limit)
            activity = measure_activity(times, commits, merges, today, settings.metrics.activity_months)
        except (CodetrailError, OSError) as error:
            logger.warning("The history isn't available: %s", error)
        try:
            scanner = SecretScanner(settings.tools)
            latest = latest_commits(mirror, end, settings.metrics.commit_window, scanner)
            types = measure_types(latest, target.metrics.types, settings.metrics.max_message_chars)
            tags = read_tags(mirror, end, scanner)
            newest = max(tags, key=lambda tag: (tag.date, tag.name), default=None)
            releases = measure_releases(tags, commit_count(mirror, newest.commit, end) if newest else None)
        except (CodetrailError, OSError) as error:
            logger.warning("The commit types and releases aren't available: %s", error)
    return RepositoryReport(activity, types, releases, size, measure_inventory(store, settings.metrics.trend_updates))
```

`values()` returns `SOURCE_FILES = Value(size.files, None)`, `CODE_LINES = Value(size.code_lines, None)`, `TEST_SHARE = Value(size.test_lines, size.code_lines)` and, with an activity, `COMMITS = Value(activity.commits, None)`, in `Metric` order. `COUNTS` in `web/documentation.py` gains the three counts. `record_metrics` builds each report in its own `try`, logs "The documentation metrics weren't recorded: …" or "The repository statistics weren't recorded: …", and records the union of what it got when it got anything.

- [ ] **Step 4: Run the tests, then the metrics suites**

Run: `mise exec -- uv run pytest tests/integration/test_repository_report.py tests/integration/test_metrics_report.py tests/unit/test_documentation_view.py -q`
Expected: PASS

- [ ] **Step 5: Commit** `feat(metrics): build the repository report and record it at each update`

---

### Task 6: the Repository page and the home card

**Files:**
- Create: `src/codetrail/web/templates/repository.html`, `tests/api/test_repository_page.py`
- Modify: `src/codetrail/web/app.py`, `src/codetrail/web/trend.py`, `src/codetrail/web/documentation.py`, `templates/home.html`, `layout/sidebar.html`, `layout/shortcuts.html`, `layout/icons.html`, `static/js/shortcuts.js`, `static/css/components.css`, `tests/api/test_shell.py`, `tests/unit/test_trend.py`, `tests/unit/test_documentation_view.py`

**Interfaces:**
- Consumes: `RepositoryReport`, `build_tiles`, `ReportCache`, `trend_points`.
- Produces: `GET /repository`; `bars(values: Sequence[int], width: int = 480, height: int = 96) -> list[tuple[float, float, float, float]]` (x, y, width, height); `age(first: date, today: date) -> tuple[int, int]` (years, months).

- [ ] **Step 1: Write the failing tests**

`test_trend.py`: `bars([0, 2, 4], width=30, height=10)` gives three bars of equal width with heights 0, 5 and 10 (scaled to the highest, a zero drawn with no height), and `bars([])` is empty. `test_documentation_view.py`: `age(date(2024, 1, 31), date(2026, 10, 10)) == (2, 8)` and `age(d, d) == (0, 0)`.

`test_repository_page.py` (fixtures as in `test_documentation_page.py`: a served target with facts, a tag `v1` whose message is "Release one", a commit by "Alice Example"):

```python
def test_the_page_needs_the_session(anonymous: TestClient) -> None:
    assert anonymous.get("/repository").status_code in (401, 403)


def test_the_page_shows_history_releases_size_and_facts(client: TestClient) -> None:
    page = client.get("/repository").text
    for expected in ("Repository", "First commit", "Release one", "v1", "Python", "Tests", "package"):
        assert expected in page
    assert "Alice Example" not in page and "author@example.com" not in page


def test_the_page_renders_before_any_fact(empty_client: TestClient) -> None:
    assert "No facts yet" in empty_client.get("/repository").text


def test_the_page_never_shows_an_excluded_path_or_a_withheld_tag(client_with_secrets: TestClient) -> None:
    page = client_with_secrets.get("/repository").text
    assert ".env" not in page and "ghp_" not in page
    assert "withheld" in page


def test_the_home_card_shows_the_repository(client: TestClient) -> None:
    assert 'href="/repository"' in client.get("/").text
```

and in `test_shell.py`, the sidebar lists `/repository` after `/documentation`.

- [ ] **Step 2: Run them to see them fail**

Run: `mise exec -- uv run pytest tests/api/test_repository_page.py tests/unit/test_trend.py tests/unit/test_documentation_view.py tests/api/test_shell.py -q`
Expected: FAIL: 404 and missing functions.

- [ ] **Step 3: Implement**
- `bars`: each value gets `width / len(values)` with a 2-unit gap; heights scale to the highest value; the y of a bar is `height - its height`.
- `age`: whole months between the dates (a day of the month not yet reached doesn't count), as years and months.
- `/repository` in `app.py`, keyed and cached like `/documentation` (`ReportCache[RepositoryReport]`), rendering `repository.html` with `report`, `tiles` (from `build_tiles` with the recorded history), `bars`, `age` and `listed`. Before any fact: the empty state with **Update the guide**; when the report raises: the "not available" state, logged.
- The home page passes `repository` (the cached report when it exists, else the `commits` and `code_lines` values last recorded) to a new `repository_card()` macro: "Started {month year} · {n} commits · {lines} lines of code", linking to **See the repository's statistics**.
- `repository.html`: the header and lead ("What the repository is: its history, releases, code and facts. Read from the code and its history, without calling your assistant."); five tiles (Age, Commits, Releases, Code, Tests); sections `#timeline` (first and latest commit, age, the bars as `<svg role="img" aria-label="Commits per month: …">` with a `<details>` table of the same numbers, years table, active weeks, longest gap, days since the latest commit, the "latest N commits" note when `cut`), `#types`, `#releases` (each release in the `capped` list, message in a `<details>`), `#size` (languages table with files, lines and a share bar; documents, other, binary and too-large counts; tests; largest files), `#facts` (kinds table with count, change and trend text; dependencies that arrived and left, each with its snapshot's date; "Codetrail's history of this repository starts on {date}"). Text from the repository is marked `lang="en" dir="ltr"`, as on the Documentation page.
- Sidebar link with a new `repository` icon under Documentation; `t: "/repository"` in `PLACES`; the shortcuts dialog row "Go to the repository"; CSS for `.bars rect` (the accent colour) beside `.trend`.

- [ ] **Step 4: Run the tests, then all API tests**

Run: `mise exec -- uv run pytest tests/api -q`
Expected: PASS

- [ ] **Step 5: Commit** `feat(web): show the repository statistics on their own page and the home card`

---

### Task 7: `codetrail stats`

**Files:**
- Modify: `src/codetrail/cli.py`
- Test: `tests/e2e/test_stats_command.py`

**Interfaces:**
- Produces: `codetrail stats <name>`, exit 0 with the summary, 1 with "No facts yet. Run: codetrail update <name> --facts-only".

- [ ] **Step 1: Write the failing test** (following `tests/e2e/test_metrics_command.py`):

```python
def test_stats_prints_the_summary_and_writes_nothing(target: Paths) -> None:
    before = database_rows(target)
    result = run_codetrail(target, "stats", "shop")
    assert result.returncode == 0
    for expected in ("First commit:", "Commits: 3 (0 merges)", "Releases: 1, latest v1", "Python:", "Tests:",
                     "Facts: "):
        assert expected in result.stdout
    assert database_rows(target) == before


def test_stats_needs_facts(empty_target: Paths) -> None:
    result = run_codetrail(empty_target, "stats", "shop")
    assert result.returncode == 1 and "No facts yet" in result.stdout
```

- [ ] **Step 2: Run it to see it fail**

Run: `mise exec -- uv run pytest tests/e2e/test_stats_command.py -q`
Expected: FAIL: unknown command.

- [ ] **Step 3: Implement** `show_stats(paths, name)` beside `show_metrics`, in the same lock and connection pattern, printing: `First commit: <date> (<years> years, <months> months ago)`, `Latest commit: <date>`, `Commits: <n> (<m> merges)`, `Releases: <n>, latest <name> on <date>, <k> commits since` or `Releases: none`, one line per language `  <language>: <files> files, <lines> lines` (the first `max_listed`), `Documents: …`, `Tests: <percent>% of <lines> lines`, and `Facts: <count> <kind>, …`. Tag names go through `printable`.

- [ ] **Step 4: Run the test**

Run: `mise exec -- uv run pytest tests/e2e/test_stats_command.py tests/e2e/test_metrics_command.py -q`
Expected: PASS

- [ ] **Step 5: Commit** `feat(metrics): print the repository statistics with codetrail stats`

---

### Task 8: translation and browser tests

**Files:**
- Modify: `src/codetrail/locales/codetrail.pot`, `src/codetrail/locales/ar/LC_MESSAGES/codetrail.po`, `.mo`; `tests/browser/test_accessibility.py`, `tests/browser/test_keyboard.py`

- [ ] **Step 1: Write the failing browser tests**: `/repository` joins the accessibility scan's pages (both themes); **G T** opens `/repository`; the bars' `aria-label` lists every month.
- [ ] **Step 2: Run them to see them fail** — `mise exec -- just test-browser` (FAIL: `/repository` not scanned and G T does nothing until the page exists; once Task 6 is in, the scan's new page must pass).
- [ ] **Step 3: Extract and translate**: `mise exec -- just catalogs`, then give every new `msgid` an Arabic `msgstr` (plural forms 0 to 5 as the catalog's header names), and `mise exec -- just catalogs` again to compile. `tests/unit/test_catalogs.py` checks markup and placeholders.
- [ ] **Step 4: Run** `mise exec -- uv run pytest tests/unit/test_catalogs.py -q` and `mise exec -- just test-browser`. Expected: PASS
- [ ] **Step 5: Commit** `feat(web): translate and test the Repository page in the browser`

---

### Task 9: docs and the whole suite

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/getting-started.md`, this plan's status line.

- [ ] **Step 1:** README's current state names the Repository page and `codetrail stats`; CHANGELOG's Unreleased gains a line; the getting-started guide mentions **G T** and `codetrail stats` next to `codetrail metrics`.
- [ ] **Step 2:** Run `mise exec -- just ci`. Expected: PASS.
- [ ] **Step 3: Commit** `docs(docs): describe the repository statistics`
- [ ] **Step 4:** Security review with the `security-reviewer` agent; fix critical and high findings in new commits; finish the branch into `develop` with the verdict in the merge message.
