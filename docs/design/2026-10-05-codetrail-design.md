# Codetrail design

- Status: approved by the founder, 5 October 2026
- Date: 2026-10-05
- Built on: [brainstorm-decisions.md](brainstorm-decisions.md) (decisions 1 to 12) and the design sessions of 5 October 2026
- Accepted ADRs: [0001](../adr/0001-pathspec-for-exclusion-rules.md) to [0005](../adr/0005-babel-for-interface-catalogs.md), [0007](../adr/0007-bundle-the-inter-typeface.md) and [0008](../adr/0008-browser-tests-with-playwright.md)

This spec describes the whole system. It is built in phases (section 13), each with its own implementation plan.

## 1. Purpose and success criteria

The founder of a large monorepo is falling behind on its architecture, patterns and tools as agents merge into `develop` every day. Codetrail tells them what changed and why it matters (need B), and teaches the engineering the way a course does (need C). It keeps a curated guide that grows with the repository, pushes what's new instead of waiting for questions, and draws diagrams only from extracted facts.

Codetrail succeeds when:

1. The page shows how far behind the guide is (merges and areas touched since the last update) without calling Claude.
2. Every node and edge in a diagram traces to a fact, and every fact to a source path and line range.
3. No file excluded by the built-in secret patterns, the ignore files or gitleaks ever reaches an extractor, Claude, the page or the guide. Tests prove it.
4. Every piece of rationale marked documented quotes a source that Codetrail has checked; everything else is marked inferred.
5. The Claude cost of an update is bounded by a page budget in configuration.
6. Adding an interface language means adding a catalog file, with no code change.

Not goals: answering one-off questions better than Claude Code (need A); a hosted or multi-user service; embeddings or retrieval over the code; writing anything into a target repository; translating generated content; updating after every merge.

## 2. Architecture

### 2.1 Shape
Codetrail is one uv project with one package, `src/codetrail/`, and tests in `tests/`. Its command line (argparse, standard library) has five commands:

| Command | Does |
|---|---|
| `codetrail target add <name> <path> [--branch <branch>]` | Writes a target's configuration with defaults |
| `codetrail target remove <name> [--yes]` | Lists everything Codetrail keeps for the target (its settings, ignore file, data and state folders), asks, then deletes them; never the repository. Refused while `serve`, `update` or `files` uses the target |
| `codetrail files <target>` | Refreshes the target's sources and lists exactly the files Codetrail can see |
| `codetrail update <target> [--yes] [--facts-only]` | Refreshes the sources, the facts and the guide, after showing the estimate (section 15.4) |
| `codetrail providers` | Shows each assistant provider, whether it is installed and signed in, and how (section 15.2) |
| `codetrail serve <target>` | Serves the page and the bridge on `127.0.0.1` and opens the browser |

During development it runs as `uv run codetrail` behind `just` recipes. For daily use the founder installs it with `uv tool install --editable .` from this repository; later releases install from a tag. Nothing is installed into or written to a target.

### 2.2 Modules
One module per part of the system. Every other module reads the target only through `repo`.

| Module | Responsibility | Depends on |
|---|---|---|
| `config` | Loads and validates the global and per-target configuration (pydantic) | — |
| `repo` | The mirror, the materialized sources, the exclusion rules, filtered logs and diffs | git, `pathspec`, gitleaks |
| `extract` | The extractor interface and one extractor per stack | `repo` |
| `facts` | The SQLite fact store with validity ranges, and diffs between snapshots | — |
| `assistant` | The interface Codetrail needs from an assistant; the `claude_code`, `codex` and `local` adapters; the fake; the tool guard; usage, prices and estimates (section 15) | the providers' programs, httpx |
| `generate` | Outline, pages, digests, checks, validation and the update budget | `facts`, `assistant`, `guide` |
| `guide` | Reads and writes the knowledge base: Markdown with YAML front matter in its own git repository | git, PyYAML |
| `web` | FastAPI routers for the pages, diagrams from facts, the "you're behind" signal, interface languages, search (section 16) | `guide`, `facts`, `learn`, `search` |
| `search` | The in-memory full-text index of the guide, saved answers, decisions and fact names (section 16.3) | `guide`, `facts` |
| `system` | The system pass: a repository's parts and the connections between them, from facts (section 17.3) | `facts`, `repo` |
| `bridge` | FastAPI router for questions, streamed answers, grading and "save to guide" | `assistant`, `guide`, `learn` |
| `learn` | Progress, check attempts, staleness and settings in SQLite | `facts`, `guide` |

### 2.3 Where things live
Codetrail follows the XDG base directories, on macOS too; configuration can move any of them.

| Path | Holds |
|---|---|
| `~/.config/codetrail/config.toml` | Global settings (server, interface, bridge, limits) |
| `~/.config/codetrail/targets/<name>.toml` | One target's settings |
| `~/.config/codetrail/targets/<name>.ignore` | The founder's ignore rules for that target |
| `~/.local/share/codetrail/<name>/mirror.git` | A bare clone of the target |
| `~/.local/share/codetrail/<name>/source/` | The allowed files at the current snapshot, with no `.git` |
| `~/.local/share/codetrail/<name>/guide/` | The knowledge base, its own git repository |
| `~/.local/share/codetrail/<name>/codetrail.db` | Facts and learning state (SQLite) |
| `~/.local/state/codetrail/<name>/codetrail.log` | The log |

The guide's location can be configured, but never inside the target.

### 2.4 Data flow
**Update** (`codetrail update`, or the page's "Update" button running the same function as a background job; one at a time per target):
1. `repo` fetches the configured branch from the founder's checkout into `mirror.git` and materializes `source/` at its head (section 3).
2. The enabled extractors produce the facts; `facts` closes the facts that disappeared and opens the new and changed ones (sections 4 and 5).
3. `generate` finds the pages whose recorded facts or scopes changed, plans pages for uncovered facts, has Claude rewrite pages within the budget, validates them and writes a digest (section 6).
4. `guide` commits the knowledge base once; only then does the snapshot become current.
5. `learn` derives staleness from the pages' recorded versions (section 8).

**Serve:** pages are rendered from the guide's Markdown, diagrams from the facts, source views from `source/`, and the "you're behind" signal from `mirror.git` after a fetch (section 8.5).

**Ask:** the page sends a question to the bridge, which runs Claude with read-only tools over `source/` and streams the answer back; the founder may save it into the guide (section 7.3).

## 3. Targets, sources and exclusions

### 3.1 Target configuration
`codetrail target add` writes `~/.config/codetrail/targets/<name>.toml`:

```toml
repository = "~/code/shop"
branch = "develop"
extractors = ["python", "adr"]
# source_url_template = "https://github.com/<owner>/<repo>/blob/{commit}/{path}#L{start}-L{end}"

[adr]
paths = ["docs/adr/*.md"]

[generation]
max_pages_per_update = 20
concurrency = 2
max_turns = 30

[models]
plan = "claude-opus-5-5"
write = "claude-sonnet-5-5"
answer = "claude-sonnet-5-5"
grade = "claude-sonnet-5-5"
```

Unknown keys are refused. Model names and every limit live here or in the global file, never in code.

### 3.2 Mirror and materialized sources
- `mirror.git` is a bare clone of the founder's checkout, made with `--no-local` over the `file://` transport and refreshed by fetching the configured branch the same way. Only git's `upload-pack` reads the checkout: no command runs inside it, and no file of it is hardlinked (a plain local clone would hardlink its objects). A `git worktree` is not used, because it writes metadata into the target's `.git`. A test snapshots every file under a checkout, `.git` included (size, times, inode, link count, hash), and proves it unchanged.
- `source/` holds only the allowed files (section 3.3) at the snapshot commit, written from git objects. It has no `.git` directory. Each refresh rebuilds it in full into `source.next/`, scans it (section 3.4) and renames it into place, so a failed refresh leaves the previous `source/` intact. `source.json`, beside it, records the commit, every file with its blob, and every exclusion with its reason. At large scale, reusing unchanged files and scanning only changed ones would make this incremental.
- Symlinks and submodules are never materialized.
- Extractors, Claude's tools and the page's source views read only `source/`. Only `repo` reads `mirror.git`.

### 3.3 Exclusion rules
Three layers decide whether a tracked file exists for Codetrail:

1. **Built-in secret patterns**, defined in code, which nothing can override. They start from the secret-read hook adapted from the founder's earlier projects:
   `.env`, `.env.*`, `.dev.vars`, `*.p8`, `*.p12`, `*.pem`, `*.keystore`, `*.jks`, `*.tfstate`, `*.tfstate.backup`,
   plus `*.tfvars`, `*.tfvars.json`, `.terraform/`, `*.key`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.ppk`, `.netrc`, `.npmrc`, `.pypirc`.
   `*.example` files (such as `.env.example`) are not secrets and stay visible.
2. **Ignore files** in exact gitignore syntax (files, `dir/`, `*`, `**`, `?`, `[…]`, leading `/` anchoring, `!` negation, `#` comments), from two sources applied in order:
   - `.codetrailignore` committed in the target, if there is one, read from the snapshot commit;
   - `~/.config/codetrail/targets/<name>.ignore`, the founder's file.

   A `!` line can re-include something an earlier ignore line excluded, but never a built-in secret pattern.
3. **Files gitleaks flags** (section 3.4).

Patterns are matched with `pathspec`'s `GitIgnoreSpec` ([ADR 0001](../adr/0001-pathspec-for-exclusion-rules.md)). If the rules can't be loaded, the refresh fails.

An excluded file is as if it were not in the repository: it isn't materialized, extracted, readable by Claude, shown in source views, or present in logs, diffs, digests or the "you're behind" counts. A commit that touched only excluded files still appears, with no files listed.

### 3.4 Content scanning
Every refresh runs gitleaks over `source.next/`, with values redacted ([ADR 0002](../adr/0002-gitleaks-on-every-update.md)). gitleaks always runs with Codetrail's own configuration (`--config`, extending the defaults), `--ignore-gitleaks-allow` and `--gitleaks-ignore-path /dev/null`, with any `GITLEAKS_*` variable removed: a probe showed that a target's own `.gitleaks.toml` otherwise hides its secrets from the scan entirely. A flagged file is removed from `source.next/` and listed in `source.json`. Commit messages and diffs are scanned as text too (section 3.3's history filtering): a flagged message is withheld, and so is a file's patch that holds a finding, which catches a secret that a commit removed from a file that is clean now. Output lists flagged files by path and rule, never by value. If gitleaks is missing or fails, the refresh fails, with what gitleaks printed (one line, control characters removed). Its executable is `[tools] gitleaks` in the global configuration. Each gitleaks run, and the `mise which` below, is stopped after `[tools] gitleaks_timeout_seconds` (10 minutes by default, at most a day), and the refresh fails, naming the setting, so a stuck program can't hold the target's update open. gitleaks runs in an empty temporary folder, since `gitleaks stdin` loads `./.gitleaksignore` from wherever it runs. A mise shim picks its version from its working folder, so Codetrail first asks `mise which` in the folder it was started from. It runs the answer only if it lies inside mise's own installs folder for the tool, since a trusted mise configuration (perhaps a target's) can name any program with a `path:` version. From mise's errors it shows only the `mise ERROR` lines, since mise quotes the configuration line it couldn't parse. `mise which` runs whatever a configuration the reader trusted there runs (templates, environment directives), as any mise-shimmed tool in that folder would. It runs in its own process group, killed whole at the time limit or when Codetrail stops early (Ctrl-C, the terminal closing, or a terminate signal, none of which reach it in its own session), so nothing such a template started keeps running. While the page is served, a scan of an answer or feedback runs in a worker thread that Codetrail waits for when it stops, so its `mise which` finishes or reaches the time limit, and is cleaned up the same way (section 15.5).

### 3.5 Changing the rules
The next refresh applies new rules. Facts from newly excluded files close, and pages built from them are rewritten. The guide's own git history keeps earlier page text: if a secret ever reached a page, scrubbing it is a deliberate manual step on that local repository.

### 3.6 Commit messages
Commit messages are scanned with gitleaks before they reach digests or Claude (section 3.4).

## 4. Fact model

### 4.1 Facts
- An **entity** is one thing in the repository. Its id is its kind plus a natural key, for example `module:services/api/app/orders.py`, `package:pypi/fastapi`, `decision:ADR-0012`. Its attributes are a JSON object.
- A **relation** is a typed edge between two entities: `(source_id, kind, target_id)` with attributes.
- Every fact has **sources**: paths relative to the repository root, each with a line range where one applies. Ids never contain line numbers, so moving code doesn't change identity. A renamed file is one fact closed and another opened.
- A fact's **hash** covers its kind, key and attributes, not its sources.

Kinds are defined in code; extractors may emit only those, so diagrams and generation know what each means. Adding a kind is a code change with a test. Phase 2 starts with:

| Entity kinds | Relation kinds |
|---|---|
| `module`, `package`, `project`, `decision` | `imports`, `depends_on`, `contains`, `supersedes` |

Later extractors add `route`, `schema`, `resource`, `terraform_module`, `environment`, `swift_target`, and `uses_schema`, `references`, `handled_by`.

### 4.2 Storage
SQLite through the standard library's `sqlite3` ([ADR 0003](../adr/0003-facts-with-validity-ranges.md)):

- `snapshots(id, commit, taken_at)`.
- `entities(id, kind, attributes, hash, sources, first_seen, last_seen)` and `relations(source_id, kind, target_id, attributes, hash, sources, first_seen, last_seen)`. `first_seen` is the snapshot a version appeared in; `last_seen` is the last snapshot it was valid in, `NULL` while current. `sources` is JSON, updated in place while the version is current.
- An update compares new facts with current ones: unchanged facts get their sources refreshed; changed facts get their current row closed and a new row opened; missing facts are closed.
- The diff for snapshot N is: added (opened at N with no row closed at N−1), changed (opened at N with a row closed at N−1), removed (closed at N−1 with nothing opened at N).
- Schema changes are ordered SQL files applied at startup and tracked with `PRAGMA user_version`.

Storage grows with churn, not with repository size.

### 4.3 What a page records
A page's front matter is the source of truth:

```yaml
id: concepts/openapi-contract-first
kind: concept
title: OpenAPI contract first
generated: true
built_at: 3f9c2e1                     # snapshot commit
facts:                                # facts the page explains directly
  - {id: "decision:ADR-0007", hash: "a41f…"}
scope:                                # everything else it covers
  paths: ["packages/contracts/"]
  kinds: ["route", "schema"]
  hash: "77d0…"                       # one hash over every current fact in scope
files:                                # files Claude opened with Read, logged by the tool guard
  - {path: "packages/contracts/openapi.json", blob: "9c1e…"}
checks: [...]                         # section 8.2
```

- A recorded fact that changed or disappeared, or a changed scope hash, makes the page **affected**: the next update rewrites it.
- A recorded file whose blob changed, with no fact change, flags the page **sources changed**: the page says so and offers regeneration, without rewriting it automatically.
- Being affected is derived from front matter against current facts, never stored as a flag, so a page that wasn't rewritten (failure or budget) is found again next time.

At large scale, an index from facts to pages in SQLite, rebuilt from front matter, would replace scanning the pages; it isn't built until a target needs it.

### 4.4 Diagrams
A diagram is a named query over facts within a scope (for example the imports inside `services/api/app`, or the infrastructure resources and their references), rendered to Mermaid text by Codetrail. Above a node limit in configuration it rolls up to directories or packages, with counts on the edges, and links to drill down. Every node and edge links to its fact. Claude never writes Mermaid. The Mermaid Codetrail writes always parses: labels are escaped, a blank label falls back to the fact's path, and an arrow is labelled only when it has a label (a browser test parses every diagram kind, with hostile names, through the vendored Mermaid). A diagram that would draw nothing is refused when a page is checked and left out when a page is shown.

### 4.5 Not facts
Commit history (read through `repo` for each update's range) and document bodies (ADRs, READMEs, design documents, which Claude reads when needed). An ADR's metadata (number, title, status, supersedes, path) is a fact, so documented rationale can link to it.

## 5. Extractors

### 5.1 Interface
```python
class Extractor(Protocol):
    name: str
    version: int  # bumped when its output changes

    def handles(self, path: str) -> bool: ...
    def prepare(self, paths: Sequence[str]) -> None: ...   # the handled paths, once, before extraction
    def extract(self, path: str, content: bytes) -> FileFacts: ...
    def resolve(self, files: Iterable[FileFacts], known: EntityIndex) -> list[Relation]: ...
```

- `prepare` receives the list of files the extractor handles, once; a Python module's name depends on the nearest `pyproject.toml`, which one file alone can't show.
- `extract` sees one file and returns entities plus unresolved references (such as `import app.db`).
- `resolve` turns references into relations against every entity known from all extractors. A reference to external code becomes an edge to a `package:` entity if one exists; otherwise it is dropped and counted in the update summary.
- A file that fails to parse produces a warning with its path; the update continues.
- Extraction is per file so that a later cache keyed by blob hash and extractor version can make it incremental without changing the interface. The cache is built when a large target needs it.
- Extractors find their own roots where a convention exists; every `pyproject.toml` marks a Python project root, so `services/api/app/db.py` is the module `app.db` in the project `services/api`.

### 5.2 The first extractors
| Phase | Extractor | Reads | Produces | Parser |
|---|---|---|---|---|
| 2 | `python` | `*.py`, `pyproject.toml` | projects, modules, packages; `contains`, `imports`, `depends_on` | tree-sitter-python, `tomllib` |
| 2 | `adr` | `[adr] paths` globs | decisions (number, title, status, date); `supersedes` | Markdown headings and status lines (standard library) |
| 7 | `openapi` | configured OpenAPI documents | routes, schemas; `uses_schema` | `json`, PyYAML (no aliases) |
| 7 | `terraform` | `*.tf` | modules (one per folder), resources; `contains`, `references` (between resources, and module calls to folders) | tree-sitter HCL grammar |
| 7 | `swift` | `Package.swift`, `import` lines in `*.swift` | Swift packages (as projects), targets and external packages; `contains`, `depends_on`, `imports` | `Package.swift` read by pattern (never executed); tree-sitter Swift grammar for imports |
| 12 | `typescript` | `*.ts`, `*.tsx`, `*.js` and their variants, `*.astro`, `package.json`, Wrangler configs | projects, modules, npm packages, Astro routes, Workers; `contains`, `imports`, `depends_on` (section 17.1) | tree-sitter TypeScript, TSX and JavaScript grammars, `json`, `tomllib` |
| 12 | `github_actions` | `.github/workflows/*.yml` and `*.yaml` | deployments (section 17.2) | PyYAML (no aliases) |

Both YAML readers share one loader, `load_yaml_without_aliases` in `codetrail.extract`: PyYAML's SafeLoader, in one pass, refusing any alias, since aliases let a file under `extract.max_file_bytes` stand for an enormous tree (a billion-laughs file) or a loop, and neither format needs them. A document using one is skipped with a warning. YAML 1.1 base-60 integers (`1:30`) stay text, as YAML 1.2 reads them, since converting a long one takes time quadratic in its length. The tree-sitter grammars come under decision 9's tree-sitter choice. A repository's SQL migrations, workflows, landing page and its design, product and planning documents are not extracted until a page needs them; Claude reads the documents for the "why".

## 6. Generation

### 6.1 What Claude writes
- **Area pages**, one per component (such as `services/api`, `apps/ios`, `infra`): what it is, how it fits with the others, its diagram.
- **Concept pages**: a pattern, tool or decision.
- **Digests**: one per update.
- **Paths** and **checks** (section 8).

Generated content is in English (decision 7). The guide's layout:

```
guide/
  outline.yaml
  areas/<id>.md
  concepts/<id>.md
  paths/<id>.md
  digests/<date>-<short commit>.md
  answers/<id>.md
```

### 6.2 Outline
On the first update, Claude receives a rolled-up view of the facts, the ADR list and the READMEs, and proposes the outline: every page's id, kind, title, scope and key facts, and the paths. Codetrail validates it (every fact id and scope must exist; ids are unique) and writes `outline.yaml`. The founder may edit it; Claude only adds to it. On later updates, facts in no page's scope go back to Claude, which extends a page's scope or proposes new pages.

### 6.3 Writing a page
Claude receives the page's outline entry, the facts in its scope, the related ADR metadata, and the filtered git log for the scope (subjects and *Why* sections). It may use Read, Grep and Glob over `source/` and nothing else. The tool guard logs each file Claude opens with Read; those paths and their blob hashes become the page's `files`. Grep and Glob results are not recorded. The page and its checks come back in one call.

### 6.4 Page syntax
Pages are CommonMark with three additions, which Codetrail processes before rendering:

- **Rationale blocks**, blockquotes whose first line marks them:
  ```markdown
  > [!documented] docs/adr/0007-rest-api-with-openapi-contract.md#L12-L18
  > "Use REST with the OpenAPI document as the contract …"

  > [!inferred]
  > The handlers stay thin, which suggests …
  ```
  A documented block cites a path with a line range, or `commit:<sha>`, and quotes it. Rationale outside these blocks is not allowed.
- **Fact links**: `[[module:app.db]]` becomes a link to the fact's view.
- **Diagram placeholders**, alone on a line: `{{diagram imports scope=services/api/app}}`.

### 6.5 Validation
A page is saved only if:
- every documented quote appears, contiguously and with whitespace normalized, in the cited lines of `source/` at the snapshot, or in the cited commit's message;
- every fact link names a current fact, and every diagram placeholder a known diagram and an existing scope;
- the front matter and checks are complete (section 8.2).

On failure Claude retries once with the errors. If it fails again, the previous page stays, the failure is listed in the update summary, and the page remains affected. It is also remembered (the `page_failures` table) with the hash of its scope at the time: later updates skip it, and say so, until something in its scope changes or the reader runs `codetrail update <target> --retry-failed`. A provider error (a timeout, a limit) isn't remembered, since it says nothing about the page.

### 6.6 Digests
Each update writes one digest from the filtered log and diffs and the fact diff: what changed, why (documented from commit *Why* sections and ADRs where they exist, inferred otherwise), and which pages changed, with links. Digests go through the same validation.

### 6.7 Budget and order
Affected pages are written in this order: pages with facts in their scope that this update added, changed or removed; then pages the founder has learned; then new pages; then the backlog, pages whose facts changed in an earlier update that didn't rewrite them (because of the cap, or because a newer Codetrail extracted more of the code). Within each group, the page with the most changed facts goes first. At most `max_pages_per_update` are written, `concurrency` at a time, each within `max_turns`. The rest stay affected for the next update. The estimate counts pages with the same selection.

### 6.8 All or nothing
Facts are recorded first: they are true whatever happens to the guide. Pages are written into the guide's working tree and committed once, at the end of the update. If the update is interrupted, crashes or loses Claude's sign-in, the uncommitted changes are discarded; pages not written stay affected, because that is derived from their front matter, and the digest covers the commits since the last digest's `to_commit`, not since the last snapshot. An update refuses to start while the guide has uncommitted changes. `codetrail update --facts-only` refreshes the facts without calling Claude.

### 6.9 The assistant interface
```python
class Assistant(Protocol):
    async def plan(self, request: PlanRequest) -> PlanDraft: ...
    async def write_page(self, request: PageRequest) -> PageDraft: ...  # includes the files read
    async def write_digest(self, request: DigestRequest) -> DigestDraft: ...
    def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]: ...
    async def grade(self, request: GradeRequest) -> Verdict: ...
```
Every draft carries the call's `Usage`. Each provider's adapter is the only code that knows that provider (section 15.1). The fake replays scripted responses and records every request. Every adapter:
- runs with `source/` as its working directory;
- loads no settings, hooks, MCP servers or `CLAUDE.md` from anywhere, so a target's own `.claude/` files, which are materialized like any other file, have no effect;
- allows Read, Grep and Glob (none for `grade`), and passes every tool call through the tool guard.

**Tool guard:** denies by default; allows only the listed tools; resolves each path to its real location and refuses anything outside `source/` or matching the exclusion rules; logs every allowed read.

**Settled by the Phase 4 spike, and kept by the `claude_code` adapter (section 15):** the guard runs as a PreToolUse hook, because hooks see every call, read-only ones included, while a permission callback can be skipped for them. Structured answers come back through Claude Code's `StructuredOutput` tool, which the guard allows: it reads nothing and takes no path. Codetrail's own system prompt replaces Claude Code's, and each call has `max_turns` and a cost limit (`max_budget_usd_per_call`).

## 7. The page and the bridge

### 7.1 The page
Server-rendered with FastAPI and Jinja2, served by uvicorn; Markdown rendered on the server by markdown-it-py with raw HTML disabled; front matter read with PyYAML's `safe_load`; Mermaid vendored at a pinned version ([ADR 0004](../adr/0004-server-rendered-page-stack.md)). A few plain JavaScript modules handle streamed answers, Mermaid, the update dialog, search, shortcuts and the Ask panel (section 16.5); there are no inline scripts.

| Route | Shows or does |
|---|---|
| `GET /` | The "you're behind" signal, unread digests, the current path, stale learned pages; until the outline exists (Phase 4), the areas are the top-level folders |
| `GET /areas/{id}`, `/concepts/{id}`, `/digests/{id}`, `/paths/{id}`, `/answers/{id}` | Guide content, with diagrams, rationale blocks and the page's status |
| `GET /facts/{id}` | A fact, its relations and its source text |
| `GET /source/{path}` | A file from `source/`, through the exclusion rules; or a link built from `source_url_template` |
| `POST /update`, `GET /update/status?after=` | Starts the background update; reports its state, and its steps after number `after` for the update panel (section 15.4) |
| `POST /settings/language` | Sets the interface language (a JSON body, so no form parser is needed and no other site's form can send it) |
| `POST /learn/...` | Marks pages and digests read (section 8) |
| `GET /login` | Exchanges the one-time code for the session (section 7.4) |
| `GET /progress`, `/answers`, `/digests`, `/search`, `/search/results`, `/bridge/answers` | Progress, saved answers, digests, search and the session's answers (section 16.1) |
| `GET /system` | The system diagram and every part with its connections and their evidence (section 17.4) |

### 7.2 Interface languages
- Interface text comes from gettext catalogs at `src/codetrail/locales/<code>/LC_MESSAGES/codetrail.po`, read at runtime by the standard library's `gettext` through Jinja2's i18n extension. English is the source and the fallback. Babel extracts and compiles them at development time ([ADR 0005](../adr/0005-babel-for-interface-catalogs.md)); compiled `.mo` files are generated by a `just` recipe and not committed.
- Each catalog declares its direction by translating `pgettext("text direction", "ltr")` as `ltr` or `rtl`, and its own name by translating `pgettext("language name", "English")`.
- The installed languages are the catalogs present. The picker lists them by their own names. The choice is stored in `learn`; the default is `ui.default_language` in the global configuration (`en`).
- `<html lang dir>` follows the chosen catalog. Guide content, which is English, is wrapped in `lang="en" dir="ltr"`.
- Errors shown in the page are catalog strings, without stack traces or file contents.
- Phase 3 ships English and a right-to-left catalog used only in tests. Each real language is added later as one catalog on its own branch.

### 7.3 The bridge
- `POST /bridge/questions` takes a question and optionally the page being read, and streams the answer with `fetch` (not `EventSource`, which can't send the token header). The page shows the stream as plain text; when it ends, the server sends the rendered, sanitized HTML that replaces it.
- The assistant configured for `answer` receives the question, the current page and its facts, the diagram placeholders this guide's facts can draw (so an answer can place real diagrams, and only those; folder and project names in the list pass a plain-path filter, `LISTABLE`, so no repository name carries text into the prompt), and the read-only tools through the guard, and is told to answer in the chosen language, given as its validated language code. The Ask button shows the question's estimate (section 15.4).
- `POST /bridge/answers/{id}/save` writes the answer to `answers/<id>.md` with front matter recording the question, its language, the snapshot and the files read. It passes the same validation as pages, except that a documented quote that can't be verified is turned into an inferred block rather than rejected. Then it's committed. Saved answers are never regenerated; they get the "sources changed" notice.
- `POST /bridge/checks/{page}/{check}` grades an answer to a check (section 8.2).
- Limits from configuration: question length, one question in flight per session, `max_turns`. Closing the page cancels the run. If Claude fails mid-answer, the stream ends with an error event and nothing is saved.

### 7.4 Security model
**Trust boundary.** The founder's browser session is trusted; other websites open in the same browser, the repository's content and anything Claude writes are not. Other processes running as the founder are inside the boundary (they can read Codetrail's files directly), so the controls below defend against the browser and the content, not local malware.

| Threat | Control |
|---|---|
| Access from another machine | The server binds `127.0.0.1` only. Configuration naming another host is refused at startup. |
| DNS rebinding | `Host` must be `127.0.0.1:<port>` or `localhost:<port>`; otherwise the request is refused. |
| Another local web server receiving the session cookie | Browsers send a cookie to every port of a host (RFC 6265), so a server on another 127.0.0.1 port that the reader visits receives it. The session ends after `server.session_minutes` (480 by default) and when `serve` stops. Such a server could also start paid work: an update or a question. That is bounded: an update has a total budget (`generation.max_budget_usd_per_update`), updates from the page wait `server.update_cooldown_seconds` after the last one, one question runs at a time with its own budget, and grading runs one at a time, `learn.grading_cooldown_seconds` apart, with a budget of `learn.max_budget_usd` per answer. Accepted on that basis; revisit with a unique `*.localhost` host name if it proves insufficient. |
| Other websites reading or driving the page | `serve` opens the browser at `/login?code=…`; the code is single-use and expires after `server.login_code_ttl_seconds`. It is exchanged for an `HttpOnly`, `SameSite=Strict` session cookie and removed from the URL. Every route except `/login` requires the session. Sessions live in memory and end when `serve` stops. |
| Cross-site request forgery | Every `POST` needs an `Origin` equal to the served origin and an `X-Codetrail-Token` header matching the per-session token, which the page reads from a `<meta>` tag. Tokens come from `secrets.token_urlsafe(32)`. |
| Script injection through repository content or Claude's output | Jinja2 autoescaping; Markdown with raw HTML disabled; only `http(s)` and relative links (markdown-it-py's link validation drops `javascript:`, `data:` and others); Mermaid labels escaped and Mermaid's `securityLevel: "strict"`; a CSP of `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'` (inline styles only because Mermaid injects `<style>` into its SVGs); `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-origin`; and `Cache-Control: no-store` on every response outside `/static/`, refusals included, unless its route sets its own, so the write token in each page's `<meta>` tag and the guide's content never stay in the browser's disk cache (ASVS v5.0.0 14.3.2). Static files keep `no-cache` (section 16.5). |
| Another site, or a server on another 127.0.0.1 port, timing or driving the page (for example probing search) | A request whose `Sec-Fetch-Site` is anything but `same-origin` or `none` (the address bar) is refused, on every route but `/static/`. |
| Prompt injection through repository content | Claude has read-only tools over `source/` and no network; every `@` in a prompt becomes a fullwidth `＠`, because Claude Code attaches the file an `@path` names before any tool call (a probe proved it); documented rationale is verified; grading has no tools at all; output is rendered inertly as above. |
| Running up cost | An estimate before every paid action, and from the page an update only with a fresh estimate id, as the reader's consent (section 15.4); the update budget, the update cooldown, one question in flight, `max_turns`, token limits for `codex` and `local`, and question length limits. |
| A provider reading the reader's credentials, or another key in the environment | Codetrail never reads keys or tokens; providers get an allowlisted environment (section 15.2). |
| Codex reading or running anything the reader can, including the target's excluded files through Codetrail's data folder | Off unless a target opts in; an empty `HOME`, `/bin/sh`, the reader's Codex configuration and the target's `AGENTS.md` switched off; outputs scanned; documented (section 15.5). |
| Claude Code's guard hook failing | Claude Code's own permission rules confine reads to `source/` independently; the hook refuses with exit code 2 on any error (section 15.1). |
| A local model's patterns or the network path to it | Grep is plain text; the endpoint is loopback only, with no proxy and no redirects (section 15.5). |
| A provider program found through a relative `PATH` entry | Absolute paths only; `source/` files are never executable (section 15.5). |
| Secrets reaching Claude or the page | Section 3, enforced twice: materialization and the tool guard. |
| Free text reaching Claude's prompt | The language code and page id are validated against the installed catalogs and the guide. |

## 8. Learning state

Paths and checks are content in the guide. Progress lives in the `learn` tables of the target's database.

### 8.1 Paths
A path is a goal and an ordered list of page ids (`paths/<id>.md`), proposed in the outline. Every step must exist. A path is rewritten when one of its pages is removed or retitled, or a page is added in its scope. "Since you last caught up" is computed, not generated: unread digests in order, then the stale pages the founder had learned.

### 8.2 Checks
Each page carries two to four open questions in its front matter, written in the same call as the page:

```yaml
checks:
  - id: why-contract-first
    question: "Why does the shop generate its app clients from the OpenAPI document instead of writing them?"
    rubric:
      - point: "The OpenAPI document is the single contract between the API and the apps"
        grounds: ["decision:ADR-0007"]
```

Every rubric point is grounded in a fact or a quoted source. The rubric is never shown in the page. Grading calls `grade` with the check, the rubric, the page and the answer, with no tools, and expects a structured verdict: `pass`, `partial` or `fail`, the rubric points missed, and feedback in the reader's language. A malformed verdict is shown as an error and never counted as a pass. Attempts are stored as `(page, check, check_hash, answer, verdict, feedback, language, at)`.

### 8.3 Progress
- A page is *unread*, *read* (the founder pressed "Mark read") or *learned* (every current check passed). Marks record the page's version: the hash of its body and checks.
- When an update rewrites a learned page, it becomes *learned, stale*. The page shows what changed since it was learned (a diff of its file from the guide's history). Checks whose hash didn't change stay passed; answering the new or changed ones makes the page *learned* again.
- A "sources changed" notice doesn't reset *learned*.
- Digests have the same *read* mark. "New since you last caught up" means unread digests and unread new pages.

### 8.4 Tables
`page_marks(page_id, read_version, learned_version, learned_at)`, `check_passes(page_id, check_id, check_hash, passed_at)`, `check_attempts(page_id, check_id, check_hash, answer, verdict, feedback, language, attempted_at)`, `digest_reads(digest_id, read_at)`, `settings(key, value)` (interface language).

### 8.5 The "you're behind" signal
On the home page, `repo` fetches into `mirror.git` without rebuilding `source/`, counts the merges on the configured branch since the last update's commit, and maps the touched paths (filtered by the exclusion rules) to areas through the outline's scopes. It never calls Claude, and the result is cached for `signal.cache_seconds`.

## 9. Configuration

The global file `~/.config/codetrail/config.toml`:

```toml
[server]
port = 8765
login_code_ttl_seconds = 60
estimate_ttl_seconds = 300
update_log_lines = 200

[ui]
default_language = "en"

[bridge]
max_question_chars = 4000
max_turns = 20
max_budget_usd = 1.0
max_session_answers = 20       # unsaved answers kept per session (section 16.4)

[search]
max_query_chars = 200
max_results = 20

[learn]
grading_cooldown_seconds = 10
max_budget_usd = 0.25

[assistant]
retry_attempts = 3

[providers.claude_code]
command = "claude"
auth = "subscription"          # or "api_key"

[providers.codex]
command = "codex"
auth = "subscription"          # or "api_key"
max_tokens_per_call = 400_000
timeout_seconds = 900

[providers.local]
base_url = "http://127.0.0.1:11434/v1"   # loopback only; LM Studio is http://127.0.0.1:1234/v1
max_turns = 30
max_tokens_per_call = 200_000
timeout_seconds = 900
max_read_bytes = 200_000

[prices]                       # USD per million tokens, API list prices, for estimates only
"claude-opus-5-5" = { input = 4.0, output = 20.0, cached_input = 0.4 }
"claude-sonnet-5-5" = { input = 2.0, output = 10.0, cached_input = 0.2 }

[estimates]                    # starting tokens per call, until Codetrail has its own history
history_size = 20
plan = { input = 60_000, output = 8_000 }
write = { input = 120_000, output = 6_000 }
digest = { input = 40_000, output = 3_000 }
answer = { input = 30_000, output = 1_500 }
grade = { input = 4_000, output = 500 }

[signal]
cache_seconds = 60

[diagrams]
max_nodes = 25

[extract]
max_file_bytes = 1_000_000   # larger files are skipped with a warning
max_attribute_chars = 300    # text taken from a file into a fact is cut to this length

[tools]
gitleaks = "gitleaks"
gitleaks_timeout_seconds = 600   # each gitleaks run, and `mise which` for a mise shim

[log]
level = "INFO"
```

The host is always `127.0.0.1`; it is not configurable. Per-target settings are in section 3.1. A key that is absent takes the value shown; every value is validated at startup.

## 10. Error handling

Security checks fail closed; an aborted update leaves nothing half-written; one failing page or file doesn't stop an update; errors are clear and never leak content.

| Situation | Behaviour |
|---|---|
| Ignore rules can't load; gitleaks missing, failing or stuck; the tool guard errors | The refresh fails; the guard denies |
| A second update for the same target | A lock file (`fcntl.flock`, released if the process dies) makes it report "already updating" |
| Removing a target that is in use | `serve` (for as long as it runs), `update` and `files` hold a shared `flock` on the target's settings file; `target remove` takes it exclusively, refuses while it's held, and holds it until it deletes that file last. A command that opened the file just before a removal finished notices that it's gone and refuses, creating no folder. Since an editor may have replaced the file `serve` or an update locked, each request to the page (but `/login` and `/static/`) also takes the shared lock on the current file, answering 410 and recreating nothing after a removal, and removal also takes an existing `update.lock`, refusing while an update holds it |
| A file fails to parse | A warning with its path; the update continues |
| A transient assistant error (rate limit, overload, network) | Retried with backoff, `assistant.retry_attempts` times |
| A provider's program is missing, signed out, or signed in differently from `auth` | The action is refused before it starts, naming the command to run |
| A provider's sign-in fails mid-update | The update stops with instructions to sign in again |
| `codex` or `local` passes its token or time limit | The process is stopped; the page fails and stays affected |
| A page, digest or answer contains something gitleaks flags | It fails like a validation error, naming the rule only |
| A page fails validation twice or hits `max_turns` | The previous page stays; listed in the summary; still affected |
| The plan's usage limit is reached | The provider's refusal ends the action; the estimate showed the usage beforehand |
| Interruption, crash or sign-in loss mid-update | Uncommitted guide changes discarded; snapshot not advanced; `source/` rebuilt if its marker doesn't match |
| Uncommitted edits in the guide | The update refuses to start |
| Invalid configuration or `outline.yaml` | Refused at startup or update, naming the key |
| Claude fails mid-answer | The stream ends with an error event; nothing saved |

Logs (standard library `logging`, at `log.level`) name paths and never contents. Claude transcripts are not logged.

## 11. Testing

Test first, always (decision 12). Suites in `tests/unit`, `tests/integration`, `tests/api`, `tests/e2e` and `tests/browser`.

- **Fixture repositories** are built in temporary folders by a helper that turns a dictionary of files and commits into a git repository; no binary fixtures are committed. The standard hostile fixture holds a `.env`, a `.tfvars`, a `.pem`, a symlink pointing outside the repository, a `.codetrailignore`, an ignore file in configuration, and a fake secret inside an ordinary `.py` file, assembled at runtime so Codetrail's own pre-commit gitleaks doesn't flag the test.
- **Exclusions:** every built-in pattern; `!.env` in an ignore file doesn't re-include `.env`; gitignore semantics (anchoring, `**`, directories, negation); excluded files, flagged files and symlinks are absent from `source/`; logs and diffs are filtered; a failed refresh leaves `source/` intact.
- **Facts:** validity ranges, the diff's added, changed and removed sets, sources refreshed in place, migrations.
- **Extractors:** each against small fixture files, including unparseable ones and unresolvable references.
- **The fake Claude** records every request, so tests assert what Claude was given (no excluded content in any prompt). Hostile scripts try to read `.env`, `../`, an absolute path and the symlink, and to call Bash, Edit, Write and web tools; the guard refuses each.
- **Generation:** validation of documented quotes, fact links and diagram placeholders; the retry; the budget and ranking; all-or-nothing on interruption; affected and "sources changed" detection.
- **The real adapters:** `claude_code` and `codex` run against fake programs (small scripts that replay recorded streams and record their arguments, environment and stdin); `local` runs against a fake endpoint (httpx's mock transport). Tests assert the read-only flags, the allowlisted environment (no key in subscription mode), stdin prompts, limits, stopping at a token limit, and usage parsing. An opt-in `just test-live` suite runs each installed provider on a fixture repository; CI skips it.
- **Estimates:** medians from history, starting values, the update's expected and maximum, refusal of an update without a fresh estimate id, and the command line's question and `--yes`.
- **API tests** (FastAPI's test client): every refusal in section 7.4 (wrong `Host`; missing or wrong `Origin`; no session; bad token; reused or expired login code; non-loopback configuration; unknown language or page id); the security headers on every response; `<script>`, `javascript:` links and hostile Mermaid labels rendered inert; streaming, cancellation and saving answers.
- **Learning:** every state transition, including *learned* → *stale* → *learned* through changed checks only; grading with pass, fail and a malformed verdict.
- **Interface languages:** every catalog against English (keys, placeholders, plural forms); every catalog compiles; CI fails if the extracted template is out of date; `dir="rtl"` from the test catalog.
- **End to end:** a fixture repository, `update` with the fake Claude, then the page through the test client: the behind signal, a page with its diagram, a graded check, and a source view refusing an excluded file.
- **Browser tests** came with the page's redesign, when its JavaScript grew: Playwright for Python against `serve` with the fake assistant, and an axe-core scan of each page type (section 16.7, ADR 0008).

Recipes: `just test-quick` (unit and API, run by the pre-push hook), `just test` (everything but live and browser), `just test-browser`, `just test-live`. `just ci` adds the browser tests, ruff, mypy strict and the repository checks.

## 12. Dependencies

| Dependency | Use | Covered by |
|---|---|---|
| Python, uv, FastAPI (with pydantic), SQLite, tree-sitter and its grammars, Mermaid | Core stack | Decision 9 |
| httpx at runtime | The `local` provider's client | ADR 0006 |
| Claude Code, Codex, Ollama or LM Studio, as the reader's own installed programs (not Python dependencies) | Assistant providers | ADR 0006 |
| mise, just, lefthook, gitleaks (as a hook), git-flow-next, Node and pnpm for commitlint, ruff, mypy, pytest | Engineering setup | Decision 11 |
| `pathspec` | Gitignore-style matching | ADR 0001 |
| gitleaks at runtime | Content scanning on every update | ADR 0002 |
| Jinja2, markdown-it-py, PyYAML, uvicorn; Mermaid vendored | The page and the guide's front matter | ADR 0004 |
| Babel (development only) | Extracting and compiling catalogs | ADR 0005 |
| Inter, bundled as font files | The page's typeface | ADR 0007 |
| pytest-playwright with Chromium, and axe-core vendored for tests (development only) | Browser and accessibility tests | ADR 0008 |
| tree-sitter-typescript, tree-sitter-javascript | The `typescript` extractor's parsers | ADR 0009 |

ADR 0003 records a storage decision and adds no dependency.

## 13. Phases

Each phase is usable on its own, has its own implementation plan, and ends with a security review and a merge into `develop`. The ADRs these phases need were accepted on 5 October 2026.

| Phase | Builds | Usable result | Needs |
|---|---|---|---|
| 0. Engineering setup | mise, `just`, the uv project skeleton, lefthook with hook scripts adapted from the founder's earlier projects, commitlint, git-flow-next, ruff, mypy, pytest; `.claude/` (deny rules, push and secret-read hooks, security-reviewer agent); `.codex/rules/`; CI; Dependabot; `docs/security/review-checklist.md` | `just ci` passes on an empty package; the "Never do" rules are enforced by tools | Decision 11 |
| 1. Targets and exclusions | `config`, `target add`, `mirror.git`, `source/`, the three exclusion layers, filtered logs and diffs, `codetrail files` | The founder checks on the first test repository that secrets and ignored files are gone, before any Claude call exists | ADRs 0001, 0002 |
| 2. Facts | The fact store, the extractor interface, `python` and `adr`, `codetrail update` printing the fact diff | A target repository's modules, packages and decisions as facts | ADR 0003 |
| 3. The page, without Claude | `serve`, the security model, interface languages, area views with roll-up diagrams, fact and source views, the "you're behind" signal | A grounded map of a target repository that says when it's behind, at no Claude cost | ADRs 0004, 0005 |
| 4. Generation | The Agent SDK spike; the `claude` interface, adapter, fake and guard; outline, area and concept pages, validation, digests, budget, update from the page | The guide: what changed, and the concepts behind it | — |
| 5. Bridge | Questions, streamed answers in the chosen language, "save to guide" | Ask from any page and keep the answers | — |
| 6. Learning | Paths, checks and grading, progress, staleness, the catch-up path | The course, and knowing when learning went stale | — |
| 7. More extractors | `openapi`, then `terraform`, then `swift_packages` | A target repository's API contract, infrastructure and Swift packages in the guide | Can start after Phase 4, alongside 5 and 6 |
| 8. Providers and sign-in | The `assistant` interface; the `claude_code`, `codex` and `local` adapters; the allowlisted environment; `codetrail providers`; usage records; output scanning | Any of the three assistants, on the reader's own subscription | ADR 0006 |
| 9. Cost estimates | Prices, starting values and history; the update, question and grading estimates; the page's dialog, the estimate id, and the command line's question | No paid action without its estimate first | Phase 8 |
| 10. Getting started | A generic README with screenshots of Codetrail's guide to itself, and the install guide | Anyone can install and run Codetrail on their own repository | Phase 9 |
| 11. The page's design | Search, the new shell and visual system, the progress and saved-answers pages, the palette, shortcuts and the Ask panel, browser tests (section 16) | A page people enjoy using, where anything in the guide is a keystroke away | ADRs 0007, 0008 |
| 12. The whole system | The `typescript` and `github_actions` extractors, the system pass, the system diagram, page and home card (section 17) | One diagram of how a repository's parts fit together, and facts for TypeScript, JavaScript and Astro code | ADR 0009 |

## 14. Answers to the brainstorm's open questions

| Question | Answer |
|---|---|
| Default home of a target's knowledge base | Codetrail's data folder, its own git repository (section 2.3) |
| Agent SDK sign-in or API key | The reader's own `claude -p` with their subscription, an API key only by choice (section 15.2, ADR 0006) |
| The fact model | Section 4 |
| The extractor interface and the first extractors | Section 5 |
| How the page is built | Server-rendered (section 7.1) |
| Checks and staleness | Sections 8.2 and 8.3 |
| Phase order | Section 13 |
| Browser tests | None at first; Playwright since the page's redesign (sections 11 and 16.7) |

## 15. Assistant providers, sign-in and cost estimates

Added on 5 October 2026 ([ADR 0006](../adr/0006-assistant-providers-and-subscriptions.md)). Codetrail works with more than one assistant, uses the reader's own subscription, and never starts paid work without first saying what it will cost.

### 15.1 Providers
The assistant is whatever plans, writes and answers (section 6.9); the kinds of call, each configured in the target's `[models]`, are `plan`, `write`, `digest`, `answer` and `grade`. Three providers implement it, each in its own adapter; nothing outside an adapter knows which one runs.

| Provider | Runs | Read-only by | Reports |
|---|---|---|---|
| `claude_code` | The reader's own `claude` program: `claude -p` in `source/`, the prompt on stdin, `--output-format stream-json` | Two independent layers. First, Claude Code's own permissions: `--tools Read,Grep,Glob`, allow rules scoped to the working folder (`Read(./**)`, `Grep(./**)`, `Glob(./**)`) and `--permission-mode dontAsk`, which refuses anything not allowed. Second, Codetrail's tool guard as a PreToolUse hook, started by the absolute path of Codetrail's own Python, with a timeout; it refuses with exit code 2 on any error of its own, since Claude Code runs a tool when a hook fails any other way (a probe on 5 October 2026 read `/etc/hosts` through a broken hook, and the permission layer alone refused it). No settings, MCP servers or slash commands of the reader's or the target's (`--setting-sources ""`, `--strict-mcp-config`, `--disable-slash-commands`) | Tokens, an estimated cost, the structured output, every tool call, and the plan's usage windows |
| `codex` | The reader's own `codex` program: `codex exec - --sandbox read-only --output-schema <file> --json --ephemeral --skip-git-repo-check` in `source/`, with the reader's Codex configuration switched off (`-c` overrides for MCP servers, notifications, project instructions and the shell environment) | Codex's read-only sandbox only: its tools are shell commands, so it can read and run anything the reader can (section 15.5). Off unless a target opts in with `[assistant] allow_codex = true` | Tokens, the structured output, the commands it ran |
| `local` | Codetrail's own tool loop against an OpenAI-compatible chat endpoint on the reader's machine: Ollama (`http://127.0.0.1:11434/v1`, the default) or LM Studio (`http://127.0.0.1:1234/v1`) | Read, Grep (a plain-text search, never a regular expression) and Glob implemented by Codetrail over `source/`, through the tool guard; the endpoint must be a loopback address | Tokens; the cost is zero |

Each kind of call names its provider and model in the target's `[models]` table, as `provider:model`; a value with no known provider prefix is a `claude_code` model, so earlier configurations keep working. A model name may itself contain colons (`local:qwen3:14b`).

Every adapter streams answers as text, returns structured drafts checked against the same JSON schemas, and returns a `Usage` (input, cached input and output tokens, the model, and the provider's own cost figure when it gives one). `codex` and `local` have no turn or dollar limit of their own, so Codetrail stops them when they pass `max_tokens_per_call` or `timeout_seconds`. Codex reports tokens only at the end of each turn, so its token limit applies per turn, and `timeout_seconds` bounds a long turn. `local` handles at most 20 tool calls in one turn and refuses the rest. `local` validates the final JSON against the schema and retries once with the errors. Prompts, schemas, the `@` neutralizing and the fences are shared by all three.

### 15.2 Sign-in
- Each provider has `auth = "subscription"` (the default) or `"api_key"`.
- Codetrail starts `claude` and `codex` with an environment built from an allowlist, and nothing else from its own environment reaches them:
  - `claude`: `PATH`, `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_*`, `TMPDIR`, `TERM`, and `CLAUDE_CONFIG_DIR` when set. On macOS its sign-in, kept in the Keychain, needs `USER` and `LOGNAME` (checked on 5 October 2026).
  - `codex`: `PATH`, `LANG`, `LC_*`, `TMPDIR`, `TERM`, `SHELL=/bin/sh`, `CODEX_HOME` (the reader's, made absolute, so the sign-in still works), and `HOME` set to an empty temporary folder, so no shell profile runs and `~` leads nowhere.
  - `PATH` loses any relative entry, and each program is resolved to an absolute path before it starts; `codetrail providers` shows it.
- With `subscription`, no key is in that environment, so the program uses its own saved sign-in (`claude` then `/login`, or `codex login`). With `api_key`, the allowlist also passes the provider's key variables by name (`ANTHROPIC_API_KEY`; `OPENAI_API_KEY` and `CODEX_API_KEY`). Codetrail never reads, logs or stores a key or a token.
- Before the first paid action of a process, and in `codetrail providers`, Codetrail runs `claude auth status --json` and `codex login status` with that environment and keeps only whether the program is signed in and how (`claude.ai` or an API key; the subscription's name). A missing program, a missing sign-in, or a sign-in that doesn't match `auth` refuses the action and names the command to run.
- `local` needs no sign-in; its check asks the endpoint for its models.
- Codetrail is for each person's own use of their own subscription. Anthropic allows a person to use the unmodified Claude Code with their own subscription and doesn't allow third-party products to offer claude.ai sign-in; Codetrail runs the reader's own `claude` and never sees its credentials (ADR 0006).

### 15.3 Usage and plan limits
- Every call's `Usage` is recorded in the target's database (`assistant_calls`: when, kind, provider, model, tokens, cost in dollars). The cost is the provider's figure when it gives one, otherwise tokens times the configured prices; `local` is zero. A call whose model has no configured price has no cost, only tokens.
- `claude_code` streams rate-limit events with the use of each plan window (five hours, seven days) and when it resets. The latest reading per window is kept (`plan_usage`), with the time it was read, and shown with every estimate. Codex has no such reading; the page links to its usage page.
- The update budget (`generation.max_budget_usd_per_update`) counts these costs for every provider, and `generation.max_tokens_per_update` (5,000,000 by default) counts tokens for every provider, so a model without a price is bounded too.

### 15.4 Estimates before paid work
An estimate is shown before every action that calls a paid provider. It never calls the assistant itself.
- **Tokens per call** are the median of the last `estimates.history_size` recorded calls of the same kind, provider and model, once there are three; before that, the starting values in `[estimates]`.
- **An update** is estimated before it starts: the plan call (when the outline is new or facts are uncovered), the pages to rewrite (the affected pages, at most `max_pages_per_update`; on the first update, the maximum), and the digest (when commits came in). It is given as expected and at most, in tokens and in dollars at the configured prices, with each call's provider and model, the sign-in in use, and the latest plan usage. It lists the pages it expects to write, in order, each with why: a new page, its outline entry changed, how many of its facts changed in this update, or catching up on facts that changed after it was written (section 6.7); and the pages skipped because they failed before (section 6.5).
- **An update estimates after its free part.** `codetrail update` and the page's Update button both refresh the sources and facts first, which calls nothing; the estimate then counts the paid calls from the fresh facts. The page's update waits with its estimate (`GET /update/status` carries it, rendered on the server for the dialog) until the reader sends the estimate's id with `POST /update/confirm`, or `POST /update/cancel`; the id works once, and after `server.estimate_ttl_seconds` the update stops, having spent nothing. The id records the reader's consent to that estimate, and is defence in depth against other websites, which can't read it. It doesn't stop another local program holding the session cookie (section 7.4); the update budget, the cooldown and one update at a time bound that.
- **The update panel shows each step as it happens.** The page's update opens a panel in the Ask panel's place, above it. Codetrail reports its own steps, the same for every provider: refreshing the facts and how many changed, planning the outline, each page started with why it's written (and retried with its problems), skipped because it failed before, written (with its provider, model, tokens and counted cost), not written (with the reason) or left for the next update, the digest, and the commit. `GET /update/status?after=N` returns the steps numbered after N, rendered on the server in the reader's language and autoescaped, since page titles and reasons come from the assistant and the repository; the server keeps the last `server.update_log_lines`, and clears them when an update starts. When the update is done the panel says so and closes, and the page reloads; when it fails or is cancelled, the panel stays open with the reason. The panel can be hidden while the update runs, and the Update button shows it again; a page opened during an update shows it with its steps so far. The model's own activity (the files it reads, its text) isn't shown yet.
- **Questions and graded checks** show their estimate on the button, such as "Ask · ~15k tokens · ≤ $0.25", without an extra click; each has its hard limit (`bridge.max_budget_usd`, `learn.max_budget_usd`).
- **`codetrail update`** prints the same estimate and asks before going on; `--yes` skips the question; without a terminal and without `--yes` it refuses. `--facts-only` calls no provider and shows no estimate.
- After each action, its actual tokens and cost are shown next to the estimate.

Prices are API list prices, in configuration with their sources, because they change. For a subscription, the dollars are what the same work would cost through the API; the real cost is the plan's usage, which the estimate shows as well.

### 15.5 Security
- The rules of sections 3 and 7.4 hold for every provider. What reaches a provider is what reached Claude before: prompts built from facts and guide pages, and `source/`.
- **Codex isn't confined to `source/`.** Its read-only sandbox prevents writes, not reads, and its tools are shell commands. Text in the target can make it read the target's excluded files through Codetrail's own data folder (`../mirror.git`, the guide, the database), read the reader's own files (keys, other repositories), and run the target's code inside the sandbox; whatever it reads goes to the provider as context before any scan. Codetrail therefore keeps Codex off unless a target opts in (`[assistant] allow_codex = true`), says so in `codetrail providers` and in every estimate that uses it, gives it an empty `HOME`, `/bin/sh` and no `USER`, switches off the reader's Codex configuration and the target's `AGENTS.md`, and scans its outputs. `claude_code` stays the default.
- **Every output is scanned for secrets** with gitleaks (Codetrail's configuration): pages, checks and digests before they are written to the guide, an answer before its final text is shown or saved, and grading feedback before it is stored. Claude Code's answers stream to the page as they are written, before the scan, since its reads are confined to `source/`; Codex's and local models' answers are buffered and shown only after it. A finding fails the page like a validation error, with no other problem, since those quote the draft and reach the retry prompt, the command line and the update panel; or it replaces the answer or feedback with a notice. Either way it names the rule only. The scan is a last check, not confinement.
- **`local` talks only to loopback.** The `base_url` is parsed and refused at startup unless its host is exactly `127.0.0.1`, `::1` or `localhost`, with no user information. The client ignores proxy settings and doesn't follow redirects. On a machine shared with other people, another account could listen on that port while the model server is down; `codetrail providers` shows the models the endpoint lists, so a stranger answering is noticed. Its file tools read at most `providers.local.max_read_bytes` per call, and Grep searches for plain text, so no pattern from the model can run away.
- **Prompts go in on stdin**, never in the command line, where other processes could read them. The settings, schema and read-log files a call needs are created with `mkstemp` outside `source/` and removed afterwards.
- **Processes are bounded:** each runs in its own process group, killed whole at `timeout_seconds` (30 seconds for a sign-in check), on cancellation, or when Codetrail stops on an exception, so nothing a program started keeps running. In its own session, a program doesn't get the terminal's Ctrl-C or hangup, or a signal sent to Codetrail's process group, so the command line turns a hangup or terminate signal into an exit that runs the same cleanup, and ignores a second signal so it can't interrupt that cleanup; a hangup ignored at start, as under `nohup`, stays ignored. git, which runs in Codetrail's own process group, is stopped on any exception (Ctrl-C included) with a terminate signal first, and killed only if it hasn't exited 5 seconds later: git removes its lock files (`index.lock` in a guide, ref locks in a mirror) on a terminate signal, while a kill would leave them to fail every later commit, reset or fetch there until removed by hand. While the page is served, an update started from it runs in a background thread, which the interpreter would abandon at exit without its cleanup, so the server stops it first, whether it shuts down (Ctrl-C, a terminate signal) or is cancelled (a hangup): a waiting estimate is declined, and the guide's writing is cancelled, which kills each program's process group and discards the guide's uncommitted changes. The server waits for that before it exits, saying so on the terminal, and a further signal, such as closing the terminal while it waits, takes effect only after it. Free work already under way, the refresh of the sources and facts, finishes first: gitleaks and `mise which` within `gitleaks_timeout_seconds`, and git with no time limit of its own. The terminal's Ctrl-C or hangup also reaches git and gitleaks, which share Codetrail's process group, so from a terminal that refresh ends early; a terminate signal sent to Codetrail alone waits for it. Scans of answers and feedback run in worker threads the interpreter waits for at exit, so a scan under way finishes or reaches its time limit, and its own cleanup runs.
- **Programs are found safely:** each provider's program is resolved to an absolute path with relative `PATH` entries removed, and files in `source/` are never executable.

## 16. The page's design: layout, search and shortcuts

Approved by the founder in the design session of 5 October 2026. The page keeps its stack (section 7.1) and its security model (section 7.4); this section changes how it looks and how it is used. The aim is a page people enjoy coming back to: it says at a glance where you were, what changed and how far you've got, and it finds anything in the guide in a keystroke.

### 16.1 Layout
The feel is a documentation site in the manner of Stripe's: calm, precise, with progress on top. Every page shares one shell of three columns, which collapse to one on narrow screens (the sidebar behind a menu button):
- **Header:** the mark, "codetrail" and the target with its branch and commit; a search box ("Search or ask… ⌘K") that opens the palette; the Ask button; the light, dark or system theme switch; the language picker; and **?** for the shortcuts.
- **Sidebar:** Home and Progress; Paths, each with a small progress bar; Areas; Concepts (the outline gives a concept no parent area, so they are listed apart); Saved answers (the newest five, then "All saved answers"); Digests; Decisions. The current page is highlighted; learned pages carry a check.
- **Reading column:** breadcrumbs; the title with its status (not read, learned, sources changed); the content, with the diagram in a framed card that can be enlarged, and documented and inferred blocks as indigo and amber callouts; checks as cards; Previous and Next within the current path. The current path is the one the reader came from (links from a path carry `?path=<id>`, which is checked against the guide), otherwise the first path holding the page; a page in no path shows neither. Reading width is capped at about 72 characters.
- **Outline:** "On this page", built from the page's headings, highlighting the section in view; hidden below about 1200 px. The article and its outline are one group, centered in the space beside the sidebar, so on a wide screen the text never hugs the sidebar and the outline stays next to the text; a page without an outline centers one wider column.
- **Ask panel:** slides in from the inline end, over the outline column, without covering the reading column on wide screens (section 16.4).
- **Update panel:** takes the same place, above the Ask panel, while an update runs; opening Ask hides it, and closing it shows Ask again (section 15.4).
- **Resizing:** the sidebar and the Ask panel have a handle on their inner edge. Drag it, or focus it and use the arrow keys (16 px a step), Home and End; double-click resets. The sidebar stays between 200 and 420 px and at most 30% of the window; the panel between 320 and 720 px and at most 45%. Neither may squeeze the reading column below 480 px: a narrower window shrinks the panels first. Widths are remembered per browser in `localStorage`; below 900 px nothing resizes. The handles are ARIA separators that report their value.

The **home page** leads with progress: the target's name, branch, commit and last update; a "Continue where you left off" card (the current path, its next unlearned page and a progress bar); three figures (how far the guide is behind the branch, with the Update button and the last update's actual cost, labelled as such, since a new estimate needs the facts refreshed first; pages learned, and how many changed since; the unread digest); your newest saved answers; then the areas.

New routes, alongside those of section 7.1:

| Route | Shows or does |
|---|---|
| `GET /progress` | Every path and page: learned, not read, or changed since learned |
| `GET /answers` | Every saved answer, newest first: its question, date and "sources changed" mark |
| `GET /digests` | Every digest, newest first, marking the unread ones (where **G** then **D** goes) |
| `POST /learn/unread` | Takes a page's read mark back (the **M** shortcut on a read page); a learned page stays learned |
| `GET /search?q=` | Search results as a page, for the header's form without JavaScript and for Enter in the palette |
| `GET /search/results?q=` | Search results as JSON, for the palette |
| `GET /bridge/answers` | This session's unsaved answers, rendered and sanitized, for the Ask panel |

Empty states invite rather than apologize ("Ask a question from any page and save the answers worth keeping"; "Run your first update", with the Update button). Errors use catalog strings, as everywhere (section 7.2).

### 16.2 Visual system
- **Tokens.** Every color, size and space is a CSS custom property on `:root`, redefined for dark mode. The page follows the system's color scheme unless the reader picks light or dark in the header; that choice is kept in `localStorage` and the page renders correctly without it. A small classic script, `static/js/theme-init.js`, loaded in `<head>` (allowed by `script-src 'self'`, so still no inline script), applies the stored choice before the page is drawn; it accepts only `light`, `dark` or `system`.

| Token | Light | Dark |
|---|---|---|
| Accent (indigo) | `#5b5bd6`, hover `#4b4bc4` | `#9b9bf5` |
| Text, muted text, faint text | `#1a1f36`, `#5c6378`, `#646b7f` | `#e6e8f0`, `#a1a8bd`, `#8b92a8` |
| Page, sidebar, card | `#ffffff`, `#f7f8fb`, `#f5f6fa` | `#0f1220`, `#141829`, `#191e31` |
| Documented callout | indigo tint | indigo tint |
| Inferred callout, stale | amber `#b26a00` and its tint | amber `#f0b255` and its tint |
| Learned | green `#18774d` | `#5fd39b` |

- **Type.** Inter (weights 400, 500 and 600) for everything, bundled with Codetrail ([ADR 0007](../adr/0007-bundle-the-inter-typeface.md)); code in the system's monospace (`ui-monospace`, SF Mono, Menlo). Body text 16 px with a line height of 1.65.
- **Shape.** A 4 px spacing scale; 8 px corners on controls and 12 px on cards; hairline borders; shadows only on what floats (the palette, dialogs and the Ask panel).
- **Motion.** 150 to 200 ms ease-out for the panel and the palette, and none when the system asks for reduced motion.
- **Accessibility.** WCAG AA contrast for every text and background pair in both themes; a visible focus ring; a "Skip to content" link; landmarks (`header`, `nav`, `main`, `aside`); a label on every icon-only button.
- **Right-to-left.** Logical CSS properties only, so the sidebar and the panel swap sides; guide content keeps `lang="en" dir="ltr"` (section 7.2).
- **Icons.** About a dozen small inline SVGs written for Codetrail; no icon font.
- **The mark.** An indigo tile holding a code prompt (`›`) and three steps rising along a trail, the last one solid: from the code to where you've got to. Chosen by the founder on 5 October 2026. The files:
  - `static/brand/mark.svg` for the header, and `static/brand/favicon.svg` for the favicon, which is the same drawing made heavier so it holds at 16 px.
  - `docs/images/logo.svg` and `logo-dark.svg` for the README: the mark with the wordmark, "codetrail" in Inter Display SemiBold tracked −0.02em, as outlines so it needs no font.
  - `docs/images/banner.svg` and `banner.png` (1280×640) for GitHub's social preview.

  Replacing these files changes the mark everywhere, with no code change. The tile is accent indigo `#5b5bd6` with white in both themes, so the header can load it as an image whatever theme the reader picks. The mark is never shown below 16 px, never recolored, and never placed with less clear space than a quarter of its width around it. The amber and green of the visual system stay out of it, since they mean inferred, stale and learned.

### 16.3 Search and the palette
- **What is searched:** the guide only. Page titles, headings and text (areas, concepts, paths, digests), saved answers, decisions, and fact names and kinds. Only a page's title and body are indexed, never its front matter, so check rubrics (never shown, section 8.2) and an answer's recorded files can't be found or quoted (such as `module:src/billing/retry.py` or `POST /charges`). Source files are not indexed, so search can never surface a file the exclusion rules hide.
- **The index:** a SQLite FTS5 table in an in-memory database, built when `serve` starts, rebuilt when an update finishes, and added to when an answer is saved. Ranking is BM25, with titles weighted above headings and headings above text. If the index can't be built, search reports that it isn't available, the log says why, and the rest of the page works.
- **Queries** never reach FTS5's query language: each word is quoted as a literal string, the last one matches as a prefix, and the query is cut to `search.max_query_chars`. The expression is passed as a bound parameter (`MATCH ?`), never written into the SQL. At most `search.max_results` results are returned.
- **Snippets** are built from plain text and sent as segments, each a piece of text and whether it matched, so no offsets have to agree between Python and JavaScript. The page shows each segment with `textContent`, matched ones inside a `<mark>` element, never by parsing markup, so no page or fact can inject markup through search.
- **The JSON routes** (`/search/results`, `/bridge/answers`) need the session cookie and the `X-Codetrail-Token` header, which the page's script sends, and answer with `Cache-Control: no-store`, so neither the session's answers nor search results stay in the browser's cache.
- **The palette** (⌘K, Ctrl+K or **/**) groups results as Pages, Saved answers, Decisions and Facts, then offers actions: **Ask about: "…"** (always the last row while there is text), **Update the guide…**, **Go to progress** and **Switch theme**. Arrow keys move, Enter opens, Esc closes. It is an ARIA combobox with a listbox, and keeps focus while open. Without JavaScript, the header's search box is a form that submits to `/search`.

### 16.4 Shortcuts and the Ask panel

| Keys | Does |
|---|---|
| ⌘K, Ctrl+K, **/** | Open the palette |
| **A** | Open the Ask panel about the current page |
| **G** then **H**, **P**, **S**, **D**, **R**, **Y** | Go to home, progress, saved answers, digests, decisions, the system |
| **[** and **]** | Previous and next page in the current path |
| **M** | Mark the page read, or unread |
| **U** | Update the guide: refresh the facts and open the estimate dialog |
| **?** | List the shortcuts |
| **Esc** | Close the palette, the panel or the dialog |

- **No shortcut spends anything.** "Ask about", **A** and **U** only open the panel or the estimate dialog; a question is sent by its Send button, which shows its estimate, and an update starts only with **Go ahead** (section 15.4).
- Single-key shortcuts are ignored while focus is in a field, select or editable area, and when ⌘, Ctrl or Alt is held (except ⌘K and Ctrl+K). A two-key sequence waits one second for its second key. The **?** dialog lists the shortcuts in the interface language.
- **The Ask panel** lists this session's unsaved answers, across page changes, from `GET /bridge/answers`: at most `bridge.max_session_answers` per session, the oldest dropped first. Nothing from an answer is kept in browser storage; only whether the panel is open is kept, per tab, in `sessionStorage`. Each answer has **Save to guide**; a saved answer appears at once in the sidebar, on `/answers` and in search. Questions still send the page being read as context.

### 16.5 Code layout
- `templates/layout/`: the shell (`shell.html`, `header.html`, `sidebar.html`, `outline.html`, `ask_panel.html`, `palette.html`, `shortcuts.html`); every page template fills its content block. New pages: `progress.html`, `answers.html`, `search.html`.
- `static/css/`: `tokens.css`, `base.css`, `layout.css`, `components.css`, `content.css` (guide pages, callouts, diagrams, code) and `print.css`, replacing `page.css`.
- `static/js/`: plain ES modules, no bundler, loaded from `main.js` as `<script type="module">` (allowed by `script-src 'self'`): `api.js` (the token header, fetch), `ask.js`, `palette.js`, `shortcuts.js`, `update.js`, `learning.js`, `outline.js`, `theme.js`, `diagram.js`. They replace `page.js`, keeping its behaviour (streamed answers, grading, the estimate dialog, marking read, Mermaid).
- `static/brand/`: the mark and favicon. `static/vendor/inter/`: the font files and their licence.
- Static files are sent with `Cache-Control: no-cache`, so the browser revalidates them on every load and an upgraded Codetrail never shows its pages with the previous version's styles or script.
- `src/codetrail/search.py`: the index and its queries; the routes live with the other page routes in `web`.

### 16.6 Configuration

```toml
[search]
max_query_chars = 200
max_results = 20

[bridge]
max_session_answers = 20
```

### 16.7 Testing
- **Unit:** query building against hostile input (quotes, `NEAR`, `*`, `-`, `^`, column filters such as `title:`, very long and non-Latin text); ranking; snippets and their highlight ranges; rebuilding after an update; adding a saved answer.
- **API:** the new routes refuse requests without the session; the JSON routes refuse requests without the token header and send `no-store`; a word found only in a check's rubric finds nothing; security headers unchanged; search never returns text from an excluded file; the shell renders its landmarks, the skip link, the sidebar's saved answers and `dir="rtl"` from the test catalog; `/bridge/answers` drops the oldest answer past its limit.
- **Browser** (new, [ADR 0008](../adr/0008-browser-tests-with-playwright.md)): Playwright for Python with headless Chromium, in `tests/browser`, against the app bound to `127.0.0.1` with the fake assistant, signed in through the real `/login?code=` link (no test-only switch in `src/`). The security policy is never bypassed: axe-core runs through `page.evaluate`, in browser contexts used only for the accessibility scans. The palette's keyboard use and its Ask action; every shortcut, and none firing while typing; the panel keeping answers across pages; Save to guide; the theme switch; **U** and **A** spending nothing; an axe-core scan of each page type in both themes, failing on any serious or critical finding. `just test-browser` runs them, and `just ci` and GitHub's CI include it.

### 16.8 Delivery
Phase 11, in four feature branches, each finished into `develop` after its security review: the search backend; the shell, the visual system and its behaviour (the progress, answers, digests and search pages, the palette, shortcuts, the Ask panel and the theme switch, which the shell can't work without); the browser tests; and the docs (new screenshots, and the README and getting-started guide covering search and shortcuts).

## 17. The whole system: parts, connections and the system diagram

Approved by the founder in the design session of 5 October 2026. Codetrail's diagrams showed one part at a time: imports in a folder, a project's packages, a folder's Terraform. Nothing showed how the parts of a repository fit together, and repositories written in TypeScript, JavaScript or Astro had no facts at all. This section adds two extractors, a system pass that finds a repository's parts and the connections between them, and a system diagram drawn from those facts. Everything is generic: it works on any repository, from whatever projects, packages, contracts, deploy configuration and infrastructure it has, and a repository with none of them gets fewer boxes, never invented ones.

### 17.1 The `typescript` extractor
- **Reads** `*.ts`, `*.tsx`, `*.mts`, `*.cts`, `*.js`, `*.jsx`, `*.mjs`, `*.cjs`, the code of `*.astro` files (the block between the `---` fences and every `<script>`), every `package.json`, `tsconfig.json`, `pnpm-workspace.yaml` (with `yaml.safe_load`) and Astro config, and Wrangler configs (`wrangler.toml`, `wrangler.json`, `wrangler.jsonc`). All of them come through `run_extractors`, so the allowed-files list and the size cap apply; the extractor opens nothing itself. It skips declaration and generated files: `*.d.ts`, `*.min.js`, and anything under `dist/`, `build/` or `.astro/`. Like every extractor it sees only committed, allowed files.
- **Projects:** each `package.json` is a project (`project:<folder>`, named by its `name`), with `dependencies`, `devDependencies` and `peerDependencies` as `package:npm/<name>`, each relation carrying its group. npm, pnpm (`pnpm-workspace.yaml`) and Yarn workspaces turn a dependency on a sibling package into a project-to-project `depends_on`.
- **Modules and imports:** each source file is a module (`module:<path>`). Static `import` and `export … from`, `import("…")` with a string literal and `require("…")` count. Relative specifiers resolve the way TypeScript does, but lexically and only against the allowed files (`posixpath.normpath`; a path that is absolute or climbs above the repository is dropped; nothing is checked on disk): the extensions above, `index` files, and the `baseUrl` and `paths` of the nearest `tsconfig.json`, whose `extends` is not followed; dependency specifiers are stored without any user or password in them; a bare specifier resolves to a workspace project when it names one, otherwise to the package the nearest `package.json` declares; anything else is counted as unresolved.
- **Routes:** in a project with an Astro config, files under `src/pages/` are routes: `.astro`, `.md` and `.mdx` pages become `GET` routes, and endpoint files (`.ts`, `.js`) one route per exported HTTP method. `[slug]` becomes `{slug}` and `[...rest]` becomes `{rest}`. Route ids carry their project folder, `route:apps/site GET /waitlist`, so they never clash with an OpenAPI document's routes.
- **Workers:** each Wrangler config is a `worker:<folder>` fact with its `name`, its entry module (a `contains` relation to it) and its bindings, kept by an allowlist: for D1 databases, KV namespaces, R2 buckets, Durable Objects, queues, service bindings and analytics datasets only the binding's name, and for routes and custom domains only their patterns, each stored as a flat string such as `d1:DB` (so the attribute length cut applies). Nothing else is read: no `vars` or `[env.*]` blocks, no `define`, no ids (`database_id`, `id`, `preview_id`, `account_id`), no Hyperdrive connection strings.
- **Parsers:** tree-sitter's TypeScript and TSX grammars and its JavaScript grammar ([ADR 0009](../adr/0009-typescript-and-javascript-grammars.md)); JSON with comments is read after stripping comments, with the standard library.

### 17.2 The `github_actions` extractor
Deploy evidence only. It reads `.github/workflows/*.yml` and `*.yaml` with a `yaml` SafeLoader that refuses aliases (a workflow using them is skipped with a warning), and records each step that deploys, with its line from the parser: `wrangler deploy`, `gcloud run deploy <service>`, `terraform apply`, `fly deploy`, `npm run deploy`, `pnpm run deploy` and `yarn deploy` (not pnpm's own `pnpm deploy`, which copies files), and the actions `cloudflare/wrangler-action`, `google-github-actions/deploy-cloudrun` and `superfly/flyctl-actions`. Each is a `deployment:<workflow path>#<job>/<index>` fact with its kind, its target name when the command or action gives one and it is a plain service name (`[A-Za-z0-9][A-Za-z0-9-]{0,62}`, so no `${{ … }}`, `$VAR` or flag value is ever kept), the job's id when it follows GitHub's job-id grammar, the folder it runs in (`working-directory` of the step, else of the job's defaults, else the repository root), and its file and line. A `working-directory` that isn't plain path segments (templated, absolute, escaping, or any other text) records no folder, and such a deployment connects to no part: an arrow is never drawn on a guess. Step text, `env` values and `secrets.*` references are never recorded.

The `terraform` extractor also records, on each resource, the values of `source_dir`, `source`, `context`, `dockerfile`, `path` and `working_dir` attributes (nested blocks too) that are one plain string of plain path segments, resolved lexically against the module's folder, as `paths`; values containing `$`, absolute paths and paths that climb above the repository are dropped.

### 17.3 The system pass
After the extractors and before the facts are recorded, `system.derive(entities, relations, manifest, read)` adds parts and their connections. It is plain code over facts and a few allowed files (read through a reader that refuses any path outside the allowed-files list and applies `extract.max_file_bytes`), so it is free, deterministic, runs with `--facts-only`, and its facts are diffed between updates like any others. It cannot fail an update: a rule that meets something unexpected skips it and adds a warning, and `codetrail update` prints how many connections each rule found. Its facts pass the same checks as extracted facts: JSON attributes, the attribute length cut and the id length limit. Likewise, an extractor that raises in `prepare` or `resolve` becomes a warning, never a failed update.

**Parts** (`part:<folder or file>`, with `kind`, `name` and `folder` attributes):

| Kind | Made from |
|---|---|
| `service` | a project that depends on a server framework (FastAPI, Starlette, Flask, Django, Express, Fastify, Hono, Koa, Vapor), has Astro endpoint routes, or is a Worker or a Cloud Run service's or Fly app's deployment target |
| `app` | a project that builds an application: a Swift executable or iOS app target, or a site (it depends on Astro, Next.js, Vite, SvelteKit or Nuxt) and isn't a service |
| `library` | every other project, Swift package or workspace package |
| `contract` | an OpenAPI document (one part per document file) |
| `infrastructure` | a Terraform module folder |
| `platform` | where things run, named from Terraform resource prefixes (`google_` Google Cloud, `aws_` AWS, `azurerm_` Azure, `cloudflare_` Cloudflare), Workers (Cloudflare) and CI deployments (`gcloud` Google Cloud, `wrangler` Cloudflare, `fly` Fly.io) |

The framework lists are part of the pass's code, each a fact-based signal (a dependency the project declares); a kind is never guessed from names. A part's kind decides how a contract connects to it: a service implements the contracts it holds or names, any other part calls through them. Every file belongs to the innermost part whose folder holds it. Managed services (Cloud Run, buckets, D1, KV) appear on their platform's part, not as parts of their own.

**Connections** are relations with `evidence: explicit` and a `source` (file and line), or `evidence: matched` with the `rule` and the two names it matched:

| Relation | Explicit | Matched |
|---|---|---|
| `depends_on` (part to part) | an existing `depends_on` or `imports` that crosses from one part into another (citing it) | — |
| `implements` (service to contract) | the contract file is inside the service's folder; or a build or config file of the service names the contract's path; or the service holds a copy of the contract with the same git blob | — |
| `calls_via` (part to contract) | the same evidence, for a part that isn't a service: generator configs, `package.json` scripts, `Package.swift`, `Makefile`, `justfile`, `*.toml`, or a same-blob copy (the Swift OpenAPI generator's `openapi.yaml`) | — |
| `deployed_on` (part to platform or infrastructure) | a Worker in the part's folder (Cloudflare); a CI deployment running in the part's folder (its platform); a Terraform resource whose `paths` fall inside the part (that infrastructure part) | a Cloud Run service or job, a Worker or a Fly app whose name contains the part's folder name as a whole word (`google_cloud_run_v2_service.api` and `services/api`) |

A client that calls through a contract a service implements is drawn with one arrow to the service, labelled with the contract; that arrow is computed when drawing, not stored. The pass never connects parts by any other means, never reads a file outside the allowed set, and never records an environment value, a secret or a step's text. Names reaching a prompt pass the plain-path filter (`LISTABLE`, section 7.3).

### 17.4 The system diagram
- **Placeholders:** `{{diagram system}}` (the whole system) and `{{diagram system focus=<folder>}}` (one part and its direct neighbours). Page checks accept them only when they would draw something; the answer's list of available diagrams puts the system diagram first; the plan and page prompts describe them.
- **Drawing:** a Mermaid flowchart, left to right. Services and apps are rounded boxes, libraries plain boxes, contracts a document shape, infrastructure a double-bordered box, platforms a stadium. `depends_on` is a plain arrow; a client's call goes to the service behind the contract, labelled with the contract, when one service implements it, and to the contract when several do, so arrows never grow as callers times services; `deployed_on` points at the platform or infrastructure. Explicit arrows are solid; matched ones dashed and labelled "matched by name". A legend under the diagram, in the interface language, explains shapes and lines. Every box links to its part's fact; "Why each arrow is there", under the diagram, lists every arrow with its evidence (a link to the file and line, or the rule and both names). Labels use the escaping and fallbacks of section 4.4, node ids are always generated (`n1`, `n2`, …), never built from names, and the browser test that parses every diagram kind with Mermaid covers every shape the system diagram uses and its dashed, labelled arrows, with hostile names.
- **Large repositories:** above `diagrams.max_nodes`, libraries roll up into their parent folder ("packages/ (12 libraries)") with counted arrows; services, apps, contracts, infrastructure and platforms never roll up. If those alone exceed the limit, they group by top-level folder, and past that the largest groups stay and the rest fold into one "other parts" box; the system page lists every part. Labels drop control and format characters, which Mermaid's shape syntax refuses.
- **Where it appears:** a "Your system" card on the home page, above the areas, linking to a new `/system` page: the diagram at full width, then every part grouped by kind with its folder, what it depends on, what calls it and where it runs, and every connection with its evidence. The sidebar lists **System** under Home and Progress; the shortcut is **G** then **Y**. Area pages show the focused system diagram for their folder, drawn from facts with no page rewritten. Answers can place it. A repository with no parts has no card and no page.

### 17.5 Configuration
The extractor names `typescript` and `github_actions` join the target's `extractors` list, `extract.max_workflow_steps` (5000) caps the steps read from one workflow and `extract.max_resource_paths` (20) the paths kept on one Terraform resource, and `extract.max_tsconfig_paths` (100) caps the path patterns and targets read from one `tsconfig.json`; a larger one is skipped with a warning, so no repository can make resolution run for hours. New targets get all seven by default; a target that lists its extractors keeps its list, and `codetrail update` says how to add the new ones. Nothing else is new; `diagrams.max_nodes` sets the roll-up.

### 17.6 Testing
- **Extractors:** each against fixture files: relative, `index`, `tsconfig` paths, workspace and bare imports, `import()` and `require`; Astro code blocks and scripts, pages, endpoints and `[slug]` routes; JSON with comments; Wrangler bindings without `vars` values; unparseable, minified and declaration files; workflows with every deploy form, a `working-directory` at step and job level, and `env` and `secrets.*` that must not appear in facts.
- **System pass:** one fixture per rule, explicit and matched; files belonging to the innermost part; a repository with no parts; hostile names.
- **Diagrams:** the system and focused diagrams, roll-up, the evidence list, the Mermaid parse test; the system page, home card and area pages in API and browser tests; the accessibility scan of the system page.
- **End to end:** a mixed fixture repository (a Python service with routes, a Swift package with a copied contract, an Astro site with a Worker, an OpenAPI contract, Terraform with a Cloud Run service named after the service, and a GitHub Actions deploy job), updated with the fake assistant; the system page shows the expected parts and arrows, solid and dashed.

### 17.7 Delivery
Phase 12, in four feature branches, each reviewed, merged with the latest `develop` and finished: the `typescript` extractor with ADR 0009; the `github_actions` extractor and the Terraform paths; the system pass; the system diagram, page, home card, area pages, answers, prompts, docs and screenshots.
