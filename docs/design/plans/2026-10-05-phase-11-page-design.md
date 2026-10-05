# Phase 11: the page's design, search and shortcuts — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a page people enjoy coming back to: a Stripe-docs-style shell with progress first, saved answers that are easy to find again, full-text search over the guide, a command palette, keyboard shortcuts and an Ask panel.

**Architecture:** the page stays server-rendered (FastAPI, Jinja2) under the same security middleware and policy. A `search` module keeps an in-memory SQLite FTS5 index of the guide, saved answers, decisions and fact names. Templates share one shell (`templates/layout/`); styles are plain CSS on design tokens; behaviour is a handful of plain ES modules served from `/static/js/`. Browser tests drive the real page with Playwright.

**Tech stack:** Python, FastAPI, Jinja2, SQLite FTS5 (standard library), plain CSS and JavaScript modules, Inter (bundled), pytest, pytest-playwright, axe-core (tests only).

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, section 16 (and 7.1 to 7.4, 8, 15.4 for what stays).

## Global constraints
- Security policy unchanged: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; ...`. No inline scripts, no outside resources.
- No shortcut, palette action or panel spends anything: a question is sent only by its Send button (with its estimate), an update starts only with Go ahead.
- Search never indexes source files; queries never reach FTS5 syntax unquoted; snippets reach the page as text.
- Every interface string goes through gettext; `just catalogs` keeps the template current. Guide content keeps `lang="en" dir="ltr"`.
- Logical CSS properties only (right-to-left catalogs).
- Settings in configuration: `search.max_query_chars` (200), `search.max_results` (20), `bridge.max_session_answers` (20).
- The mark lives in exactly three files: `static/brand/mark.svg`, `static/brand/favicon.svg`, `docs/images/logo.svg`.
- Existing routes and their behaviour stay; existing tests keep passing (templates may change the markup they assert on, never the behaviour).

## Review focus
- A search for `title:x`, `"`, `NEAR(a b)`, `a OR b`, `-x`, `*`, `^x`, an emoji or 500 characters: no error, results as plain words.
- A guide with no pages, no paths, no saved answers and no database yet: every new page renders an inviting empty state, no 500.
- A saved answer whose front matter was hand-edited to be malformed (no `asked_at`, a list as the title): listed, sorted, never a 500.
- A key pressed while typing in the Ask box, a check's answer or the palette: no shortcut fires.
- More unsaved answers than `bridge.max_session_answers`: the oldest is dropped, saving a dropped one says it's gone.

---

## Branch 1: `feature/search` — the search backend

### Task 1: settings
**Files:** `src/codetrail/config.py`; test `tests/unit/test_config.py`.
- [ ] Tests: defaults `search.max_query_chars == 200`, `search.max_results == 20`, `bridge.max_session_answers == 20`; zero or negative refused; values read from `config.toml`.
- [ ] `SearchSettings(max_query_chars: int = Field(200, gt=0, le=1000), max_results: int = Field(20, gt=0, le=100))`, `GlobalConfig.search`; `BridgeSettings.max_session_answers: int = Field(20, gt=0, le=200)`.

### Task 2: the index
**Files:** `src/codetrail/search.py`; test `tests/unit/test_search.py`.
**Interfaces:**
```python
@dataclass(frozen=True)
class Document:
    id: str          # a page id, "facts/<fact id>" or "decisions/<fact id>"
    kind: str        # "area", "concept", "path", "digest", "answer", "decision" or "fact"
    title: str
    headings: str    # the body's headings, joined by newlines
    text: str        # the body as plain text (Markdown markers and rationale markers removed)
    link: str        # where the result opens: /pages/<id>, /facts/<id>

@dataclass(frozen=True)
class Result:
    document: Document
    snippet: list[tuple[str, bool]]    # plain-text segments, at most ~160 characters, and whether each matched

def match_expression(query: str, max_chars: int) -> str | None   # None when no word is left
class SearchIndex:
    def __init__(self) -> None
    def rebuild(self, documents: Iterable[Document]) -> None
    def add(self, document: Document) -> None
    def search(self, query: str, max_chars: int, limit: int) -> list[Result]
def guide_documents(guide: GuideRepository, store: FactStore | None) -> list[Document]
```
- [ ] Tests for `match_expression`: words become `"word"` joined by spaces, the last as `"word"*`; inner `"` doubled; `'` kept as text (the expression is always a bound parameter, `MATCH ?`); FTS5 operators (`AND`, `OR`, `NOT`, `NEAR`), `-`, `*`, `^`, `:`, parentheses stay literal; cut to `max_chars`; empty and punctuation-only queries give `None`; non-Latin words kept.
- [ ] Tests for `SearchIndex`: a title match ranks above a body match (bm25 weights 10, 4, 1 for title, headings, text); prefix `retr` finds "retry"; `limit` respected; `rebuild` replaces everything; `add` replaces a document with the same id; every hostile query from the review focus returns a list without raising; snippets join back to plain text from the document, and every matched segment is a matched word, also after an emoji.
- [ ] Tests for `guide_documents`: areas, concepts, paths, digests and answers from the guide, title and body only (a word only in a check's rubric or an answer's front matter is never indexed); facts as `fact` documents titled by id with their kind; decisions as `decision` documents with number and title; malformed front matter (non-string title, missing fields) still gives a document; nothing is read from `source/`.
- [ ] Implementation: `sqlite3.connect(":memory:", check_same_thread=False)`, a `threading.Lock` around every use, `CREATE VIRTUAL TABLE documents USING fts5(title, headings, text, id UNINDEXED, kind UNINDEXED, link UNINDEXED, tokenize='unicode61 remove_diacritics 2')`; results by `bm25(documents, 10.0, 4.0, 1.0)`; snippets built in Python from `text` around the first matched word, so ranges are exact and nothing is parsed as markup.

### Task 3: routes
**Files:** `src/codetrail/web/app.py`, `src/codetrail/bridge.py`, `src/codetrail/update.py` (nothing; the app rebuilds after its job), test `tests/api/test_search.py`.
**Interfaces:** `GET /search/results?q=` → `{"results": [{"title", "kind", "link", "snippet": [[text, matched], ...]}], "available": bool}`; `GET /search?q=` → `search.html`; the app keeps one `SearchIndex`, rebuilt at start-up, after the update job finishes (done or declined), and added to by a saved answer (the bridge takes an `on_saved(page: Page)` callback).
- [ ] Tests: both routes need the session (403 without); `/search/results` also needs the token header (403 without); results for a page title, a heading word, a saved answer and a fact; an excluded file's name and contents are never in results (fixture with `.env` content and a hidden path); an answer saved through the bridge is found at once; a failing index build leaves `available: false` and every other page working; the JSON route sends `Cache-Control: no-store`; a query holding `'` works.

### Task 4: the session's answers
**Files:** `src/codetrail/bridge.py`; test `tests/api/test_bridge.py`.
**Interfaces:** `GET /bridge/answers` → `{"answers": [{"id", "question", "html", "asked_at"}]}`, oldest first; `BridgeState.answers` keeps at most `max_session_answers`, dropping the oldest.
- [ ] Tests: lists unsaved answers with server-rendered HTML; needs the token header; sends `Cache-Control: no-store`; a saved answer leaves the list; past the limit the oldest is dropped and saving it answers 404 "That answer is gone; ask again."; needs the session.

### Task 5: finish
- [ ] Design and README current-state lines; `just ci`; security review; `git flow feature finish search`.

---

## Branch 2: `feature/page-shell` — the shell, the visual system and the new pages

### Task 6: assets
**Files:** `static/vendor/inter/` (`InterVariable.woff2`, `InterVariable-Italic.woff2`, `LICENSE.txt`, `VERSION` with SHA-256 sums), `static/brand/mark.svg`, `static/brand/favicon.svg`, `docs/images/logo.svg`; test `tests/unit/test_static_assets.py`.
- [ ] Tests: the font files match the sums in `VERSION`; the SVGs parse as XML and contain no `<script>`, no `on*` attribute and no external reference.

### Task 7: styles
**Files:** `static/css/tokens.css`, `base.css`, `layout.css`, `components.css`, `content.css`, `print.css`; remove `static/page.css`.
- [ ] Tokens from design 16.2, light on `:root`, dark under `@media (prefers-color-scheme: dark)` for `:root:not([data-theme="light"])` and again for `:root[data-theme="dark"]`; `@font-face` for Inter with `font-display: swap`; reduced motion; logical properties only.
- [ ] Test (in `tests/api/test_shell.py`): no stylesheet uses `left`, `right`, `margin-left`, `margin-right`, `padding-left`, `padding-right`, `text-align: left|right` or `border-left|right` (a grep over `static/css/`).

### Task 8: the shell
**Files:** `templates/layout/{shell,header,sidebar,outline,ask_panel,palette,shortcuts}.html`, `templates/base.html` becomes the shell's entry point, every page template; `web/app.py` gives every page a `navigation` context (paths with progress, areas, concepts, the newest five answers, the current page id); test `tests/api/test_shell.py`.
**Interfaces:**
```python
@dataclass(frozen=True)
class Navigation:
    paths: list[tuple[Page, int, int]]   # path, learned, total
    areas: list[Page]
    concepts: list[Page]
    answers: list[Page]                  # newest five, by asked_at
    answer_count: int
    statuses: dict[str, str]             # page id -> learning state
def navigation(guide: GuideRepository, state: LearningState | None) -> Navigation
def answers_newest_first(pages: list[Page]) -> list[Page]   # malformed asked_at sorts last
```
- [ ] Tests: every page has `header`, `nav`, `main`, the skip link to `#content`, the search form (`action="/search"`), the Ask panel, the palette and the shortcuts dialog; the sidebar lists paths with progress, areas, concepts and the newest saved answers with "All saved answers"; `aria-current="page"` on the current page; `dir="rtl"` from the test catalog; the favicon link; only `/static/js/main.js` as a module script and the vendored Mermaid; no inline script or event handler attribute anywhere.

### Task 9: the home page
**Files:** `templates/home.html`, `web/app.py`; test `tests/api/test_pages.py`, `tests/api/test_guide_pages.py`.
- [ ] Tests: "Continue where you left off" names the first path with an unlearned step and links that step with `?path=`; the three figures (behind, learned and stale counts, unread digest); the last update's actual cost from `assistant_calls`, labelled; newest saved answers; areas; with no guide, "Run your first update" with the Update button; with no database, still 200.

### Task 10: progress, answers and search pages; path navigation
**Files:** `templates/progress.html`, `answers.html`, `search.html`, `page.html`; `web/app.py`; `learn/__init__.py` (`mark_unread`), `learn/routes.py` (`POST /learn/unread`); tests `tests/api/test_learning_pages.py`, `tests/api/test_search.py`, `tests/unit/test_learn.py`.
- [ ] Tests: `/progress` lists each path's steps with their state and the pages changed since learned; `/answers` lists every saved answer newest first with "sources changed" where its files changed, and an inviting empty state; `/search?q=` renders results with highlights as `<mark>` around escaped text; a page opened with a valid `?path=` shows Previous and Next from that path, an unknown or invalid one falls back to the first path holding the page, and a page in no path shows neither; `mark_unread` turns read back to unread and leaves learned alone; `POST /learn/unread` needs the token.

### Task 11: finish
- [ ] Catalogs; screenshots unchanged until branch 4; `just ci`; security review; finish.

---

## Branch 3: `feature/page-interaction` — the palette, shortcuts, the Ask panel and browser tests

### Task 12: the modules
**Files:** `static/js/{main,api,ask,palette,shortcuts,update,learning,outline,theme,diagram}.js`, and the classic `static/js/theme-init.js` loaded in `<head>` (only `light`, `dark` or `system` accepted); remove `static/page.js`.
**Interfaces:** `api.js` exports `post(url, data)`, `getJSON(url)`, `usedText(labels, usage)`; `ask.js` exports `openAsk(question?)`; `palette.js` exports `openPalette()`; `update.js` exports `startUpdate()`; `learning.js` exports `toggleRead()`; `theme.js` exports `cycleTheme()`.
- [ ] Behaviour kept from `page.js`: streamed answers as text then server HTML, Save to guide, grading feedback as text, the estimate dialog and its id, marking read, the language picker, Mermaid in strict mode (theme follows light or dark).
- [ ] New: the palette (debounced `GET /search/results`, groups, actions, ARIA combobox, highlights built with `textContent` and `<mark>` elements); the shortcuts map of design 16.4 with the typing and modifier rules; the Ask panel loading `GET /bridge/answers`, keeping open per tab in `sessionStorage`; the outline from `h2`/`h3` with `IntersectionObserver`; the theme switch in `localStorage` (try/catch); every storage access wrapped so the page works without it.

### Task 13: browser tests
**Files:** `pyproject.toml`, `uv.lock` (`pytest-playwright` in `dev`), `justfile` (`test-browser`, `ci` includes it, `setup` installs Chromium), `.github/workflows/ci.yml`, `tests/browser/conftest.py` (a fixture repository updated with the fake assistant, `create_app` under uvicorn bound to `127.0.0.1` on a free port in a thread, a page signed in through the real `/login?code=` link; never `bypass_csp`; axe-core through `page.evaluate` in its own contexts), `tests/browser/vendor/axe.min.js` with licence and `VERSION`, `tests/browser/test_*.py`; `pytest` marker `browser`, excluded from `just test` and `test-quick`.
- [ ] Tests: ⌘K and `/` open the palette, typing finds a page, arrows and Enter open it, Esc closes; "Ask about" opens the panel with the text and sends nothing (the fake records no request); **A** opens the panel; G-then-H/P/S/D/R navigate; `[` and `]` follow the path; **M** marks read; **U** opens the estimate dialog and Cancel leaves the fake with no page request; **?** lists the shortcuts; no shortcut fires while typing in the Ask box, a check or the palette; an answer asked on one page is still in the panel on another; Save to guide adds it to the sidebar and to search; the theme switch flips `data-theme` and survives a reload; no console errors and no CSP violations on any page; axe-core finds no serious or critical issue on home, a guide page, progress, answers, search, a fact and a source page, in light and dark.

### Task 14: finish
- [ ] Catalogs; `just ci` (with the browser tests); security review; finish.

---

## Branch 4: `feature/page-docs` — docs and screenshots

### Task 15: docs
**Files:** `README.md`, `docs/getting-started.md`, `docs/images/*.png`.
- [ ] New screenshots of Codetrail's guide to itself (home, a guide page with the outline, the palette, the Ask panel, the estimate dialog), taken with Playwright, light theme, 1440×900; the README shows the logo and lists search and the shortcuts, credits Inter; the getting-started guide's "Open the page" section covers search, the palette, shortcuts and saved answers.
- [ ] `just ci`; security review; finish; report to the founder, who sends the logo before pushing.
