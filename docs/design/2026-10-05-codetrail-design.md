# Codetrail design

- Status: approved by the founder, 5 October 2026
- Date: 2026-10-05
- Built on: [brainstorm-decisions.md](brainstorm-decisions.md) (decisions 1 to 12) and the design sessions of 5 October 2026
- Accepted ADRs: [0001](../adr/0001-pathspec-for-exclusion-rules.md) to [0005](../adr/0005-babel-for-interface-catalogs.md)

This spec describes the whole system. It is built in phases (section 13), each with its own implementation plan.

## 1. Purpose and success criteria

The founder of `hamesh-monorepo` is falling behind on its architecture, patterns and tools as agents merge into `develop` every day. Codetrail tells them what changed and why it matters (need B), and teaches the engineering the way a course does (need C). It keeps a curated guide that grows with the repository, pushes what's new instead of waiting for questions, and draws diagrams only from extracted facts.

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
| `web` | FastAPI routers for the pages, diagrams from facts, the "you're behind" signal, interface languages | `guide`, `facts`, `learn` |
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
repository = "~/PersonalProjects/hamesh/hamesh-monorepo"
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

1. **Built-in secret patterns**, defined in code, which nothing can override. They start from Hamesh's `block_secret_reads.py`:
   `.env`, `.env.*`, `.dev.vars`, `*.p8`, `*.p12`, `*.pem`, `*.keystore`, `*.jks`, `*.tfstate`, `*.tfstate.backup`,
   plus `*.tfvars`, `*.tfvars.json`, `.terraform/`, `*.key`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.ppk`, `.netrc`, `.npmrc`, `.pypirc`.
   As in Hamesh, `*.example` files (such as `.env.example`) are not secrets and stay visible.
2. **Ignore files** in exact gitignore syntax (files, `dir/`, `*`, `**`, `?`, `[…]`, leading `/` anchoring, `!` negation, `#` comments), from two sources applied in order:
   - `.codetrailignore` committed in the target, if there is one, read from the snapshot commit;
   - `~/.config/codetrail/targets/<name>.ignore`, the founder's file.

   A `!` line can re-include something an earlier ignore line excluded, but never a built-in secret pattern.
3. **Files gitleaks flags** (section 3.4).

Patterns are matched with `pathspec`'s `GitIgnoreSpec` ([ADR 0001](../adr/0001-pathspec-for-exclusion-rules.md)). If the rules can't be loaded, the refresh fails.

An excluded file is as if it were not in the repository: it isn't materialized, extracted, readable by Claude, shown in source views, or present in logs, diffs, digests or the "you're behind" counts. A commit that touched only excluded files still appears, with no files listed.

### 3.4 Content scanning
Every refresh runs gitleaks over `source.next/`, with values redacted ([ADR 0002](../adr/0002-gitleaks-on-every-update.md)). gitleaks always runs with Codetrail's own configuration (`--config`, extending the defaults), `--ignore-gitleaks-allow` and `--gitleaks-ignore-path /dev/null`, with any `GITLEAKS_*` variable removed: a probe showed that a target's own `.gitleaks.toml` otherwise hides its secrets from the scan entirely. A flagged file is removed from `source.next/` and listed in `source.json`. Commit messages and diffs are scanned as text too (section 3.3's history filtering): a flagged message is withheld, and so is a file's patch that holds a finding, which catches a secret that a commit removed from a file that is clean now. Output lists flagged files by path and rule, never by value. If gitleaks is missing or fails, the refresh fails. Its executable is `[tools] gitleaks` in the global configuration.

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
A diagram is a named query over facts within a scope (for example the imports inside `services/api/app`, or the infrastructure resources and their references), rendered to Mermaid text by Codetrail. Above a node limit in configuration it rolls up to directories or packages, with counts on the edges, and links to drill down. Every node and edge links to its fact. Claude never writes Mermaid.

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

### 5.2 Extractors for Hamesh
| Phase | Extractor | Reads | Produces | Parser |
|---|---|---|---|---|
| 2 | `python` | `*.py`, `pyproject.toml` | projects, modules, packages; `contains`, `imports`, `depends_on` | tree-sitter-python, `tomllib` |
| 2 | `adr` | `[adr] paths` globs | decisions (number, title, status, date); `supersedes` | Markdown headings and status lines (standard library) |
| 7 | `openapi` | configured OpenAPI documents | routes, schemas; `uses_schema` | `json` |
| 7 | `terraform` | `*.tf` | modules (one per folder), resources; `contains`, `references` (between resources, and module calls to folders) | tree-sitter HCL grammar |
| 7 | `swift` | `Package.swift`, `import` lines in `*.swift` | Swift packages (as projects), targets and external packages; `contains`, `depends_on`, `imports` | `Package.swift` read by pattern (never executed); tree-sitter Swift grammar for imports |

The tree-sitter grammars come under decision 9's tree-sitter choice. Hamesh's SQL migrations, workflows, landing page and the design, PRD and slice documents are not extracted until a page needs them; Claude reads the documents for the "why".

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

On failure Claude retries once with the errors. If it fails again, the previous page stays, the failure is listed in the update summary, and the page remains affected.

### 6.6 Digests
Each update writes one digest from the filtered log and diffs and the fact diff: what changed, why (documented from commit *Why* sections and ADRs where they exist, inferred otherwise), and which pages changed, with links. Digests go through the same validation.

### 6.7 Budget and order
Affected pages are ranked by how many of their facts changed, with pages the founder has learned first. At most `max_pages_per_update` are written, `concurrency` at a time, each within `max_turns`. The rest stay affected for the next update.

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
Server-rendered with FastAPI and Jinja2, served by uvicorn; Markdown rendered on the server by markdown-it-py with raw HTML disabled; front matter read with PyYAML's `safe_load`; Mermaid vendored at a pinned version ([ADR 0004](../adr/0004-server-rendered-page-stack.md)). One small plain-JavaScript file handles streamed answers, Mermaid and the update button; there are no inline scripts.

| Route | Shows or does |
|---|---|
| `GET /` | The "you're behind" signal, unread digests, the current path, stale learned pages; until the outline exists (Phase 4), the areas are the top-level folders |
| `GET /areas/{id}`, `/concepts/{id}`, `/digests/{id}`, `/paths/{id}`, `/answers/{id}` | Guide content, with diagrams, rationale blocks and the page's status |
| `GET /facts/{id}` | A fact, its relations and its source text |
| `GET /source/{path}` | A file from `source/`, through the exclusion rules; or a link built from `source_url_template` |
| `POST /update`, `GET /update/status` | Starts the background update; streams its progress |
| `POST /settings/language` | Sets the interface language (a JSON body, so no form parser is needed and no other site's form can send it) |
| `POST /learn/...` | Marks pages and digests read (section 8) |
| `GET /login` | Exchanges the one-time code for the session (section 7.4) |

### 7.2 Interface languages
- Interface text comes from gettext catalogs at `src/codetrail/locales/<code>/LC_MESSAGES/codetrail.po`, read at runtime by the standard library's `gettext` through Jinja2's i18n extension. English is the source and the fallback. Babel extracts and compiles them at development time ([ADR 0005](../adr/0005-babel-for-interface-catalogs.md)); compiled `.mo` files are generated by a `just` recipe and not committed.
- Each catalog declares its direction by translating `pgettext("text direction", "ltr")` as `ltr` or `rtl`, and its own name by translating `pgettext("language name", "English")`.
- The installed languages are the catalogs present. The picker lists them by their own names. The choice is stored in `learn`; the default is `ui.default_language` in the global configuration (`en`).
- `<html lang dir>` follows the chosen catalog. Guide content, which is English, is wrapped in `lang="en" dir="ltr"`.
- Errors shown in the page are catalog strings, without stack traces or file contents.
- Phase 3 ships English and a right-to-left catalog used only in tests. Each real language is added later as one catalog on its own branch.

### 7.3 The bridge
- `POST /bridge/questions` takes a question and optionally the page being read, and streams the answer with `fetch` (not `EventSource`, which can't send the token header). The page shows the stream as plain text; when it ends, the server sends the rendered, sanitized HTML that replaces it.
- The assistant configured for `answer` receives the question, the current page and its facts, and the read-only tools through the guard, and is told to answer in the chosen language, given as its validated language code. The Ask button shows the question's estimate (section 15.4).
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
| Script injection through repository content or Claude's output | Jinja2 autoescaping; Markdown with raw HTML disabled; only `http(s)` and relative links (markdown-it-py's link validation drops `javascript:`, `data:` and others); Mermaid labels escaped and Mermaid's `securityLevel: "strict"`; a CSP of `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'` (inline styles only because Mermaid injects `<style>` into its SVGs); `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`. |
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
    question: "Why does Hamesh generate its app clients from the OpenAPI document instead of writing them?"
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

[ui]
default_language = "en"

[bridge]
max_question_chars = 4000
max_turns = 20
max_budget_usd = 1.0

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
page = { input = 120_000, output = 6_000 }
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

[log]
level = "INFO"
```

The host is always `127.0.0.1`; it is not configurable. Per-target settings are in section 3.1. A key that is absent takes the value shown; every value is validated at startup.

## 10. Error handling

Security checks fail closed; an aborted update leaves nothing half-written; one failing page or file doesn't stop an update; errors are clear and never leak content.

| Situation | Behaviour |
|---|---|
| Ignore rules can't load; gitleaks missing or failing; the tool guard errors | The refresh fails; the guard denies |
| A second update for the same target | A lock file (`fcntl.flock`, released if the process dies) makes it report "already updating" |
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

Test first, always (decision 12). Suites in `tests/unit`, `tests/integration`, `tests/api` and `tests/e2e`.

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
- **No browser tests** at first: the page is server-rendered and its JavaScript small. If the JavaScript grows, Playwright comes with a proposed ADR.

Recipes: `just test-quick` (unit and API, run by the pre-push hook), `just test` (everything but live), `just test-live`. `just ci` adds ruff, mypy strict and the repository checks.

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

ADR 0003 records a storage decision and adds no dependency.

## 13. Phases

Each phase is usable on its own, has its own implementation plan, and ends with a security review and a merge into `develop`. The ADRs these phases need were accepted on 5 October 2026.

| Phase | Builds | Usable result | Needs |
|---|---|---|---|
| 0. Engineering setup | mise, `just`, the uv project skeleton, lefthook with Hamesh's hook scripts adapted, commitlint, git-flow-next, ruff, mypy, pytest; `.claude/` (deny rules, push and secret-read hooks, security-reviewer agent); `.codex/rules/`; CI; Dependabot; `docs/security/review-checklist.md` | `just ci` passes on an empty package; the "Never do" rules are enforced by tools | Decision 11 |
| 1. Targets and exclusions | `config`, `target add`, `mirror.git`, `source/`, the three exclusion layers, filtered logs and diffs, `codetrail files` | The founder checks on Hamesh that secrets and ignored files are gone, before any Claude call exists | ADRs 0001, 0002 |
| 2. Facts | The fact store, the extractor interface, `python` and `adr`, `codetrail update` printing the fact diff | Hamesh's modules, packages and decisions as facts | ADR 0003 |
| 3. The page, without Claude | `serve`, the security model, interface languages, area views with roll-up diagrams, fact and source views, the "you're behind" signal | A grounded map of Hamesh that says when it's behind, at no Claude cost | ADRs 0004, 0005 |
| 4. Generation | The Agent SDK spike; the `claude` interface, adapter, fake and guard; outline, area and concept pages, validation, digests, budget, update from the page | The guide: what changed, and the concepts behind it | — |
| 5. Bridge | Questions, streamed answers in the chosen language, "save to guide" | Ask from any page and keep the answers | — |
| 6. Learning | Paths, checks and grading, progress, staleness, the catch-up path | The course, and knowing when learning went stale | — |
| 7. More extractors | `openapi`, then `terraform`, then `swift_packages` | Hamesh's contract, infrastructure and iOS packages in the guide | Can start after Phase 4, alongside 5 and 6 |
| 8. Providers and sign-in | The `assistant` interface; the `claude_code`, `codex` and `local` adapters; the allowlisted environment; `codetrail providers`; usage records; output scanning | Any of the three assistants, on the reader's own subscription | ADR 0006 |
| 9. Cost estimates | Prices, starting values and history; the update, question and grading estimates; the page's dialog, the estimate id, and the command line's question | No paid action without its estimate first | Phase 8 |
| 10. Getting started | A generic README with screenshots of Codetrail's guide to itself, and the install guide | Anyone can install and run Codetrail on their own repository | Phase 9 |

## 14. Answers to the brainstorm's open questions

| Question | Answer |
|---|---|
| Default home of a target's knowledge base | Codetrail's data folder, its own git repository (section 2.3) |
| Agent SDK sign-in or API key | The reader's own `claude -p` with their subscription, an API key only by choice (section 15.2, ADR 0006) |
| The fact model | Section 4 |
| The extractor interface and Hamesh's first extractors | Section 5 |
| How the page is built | Server-rendered (section 7.1) |
| Checks and staleness | Sections 8.2 and 8.3 |
| Phase order | Section 13 |
| Browser tests | None at first (section 11) |

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

Every adapter streams answers as text, returns structured drafts checked against the same JSON schemas, and returns a `Usage` (input, cached input and output tokens, the model, and the provider's own cost figure when it gives one). `codex` and `local` have no turn or dollar limit of their own, so Codetrail stops them when they pass `max_tokens_per_call` or `timeout_seconds`. `local` validates the final JSON against the schema and retries once with the errors. Prompts, schemas, the `@` neutralizing and the fences are shared by all three.

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
- The update budget (`generation.max_budget_usd_per_update`) counts these costs for every provider.

### 15.4 Estimates before paid work
An estimate is shown before every action that calls a paid provider. It never calls the assistant itself.
- **Tokens per call** are the median of the last `estimates.history_size` recorded calls of the same kind, provider and model, once there are three; before that, the starting values in `[estimates]`.
- **An update** is estimated before it starts: the plan call (when the outline is new or facts are uncovered), the pages to rewrite (the affected pages, at most `max_pages_per_update`; on the first update, the maximum), and the digest (when commits came in). It is given as expected and at most, in tokens and in dollars at the configured prices, with each call's provider and model, the sign-in in use, and the latest plan usage.
- **The page's Update button** fetches `GET /update/estimate` and shows it in a dialog. Proceeding sends the estimate's id with `POST /update`; an update without a fresh estimate id from the same session (valid `server.estimate_ttl_seconds`) is refused. The id records the reader's consent to that estimate, and is defence in depth against other websites, which can't read it. It doesn't stop another local program holding the session cookie (section 7.4); the update budget, the cooldown and one update at a time bound that.
- **Questions and graded checks** show their estimate on the button, such as "Ask · ~15k tokens · ≤ $0.25", without an extra click; each has its hard limit (`bridge.max_budget_usd`, `learn.max_budget_usd`).
- **`codetrail update`** prints the same estimate and asks before going on; `--yes` skips the question; without a terminal and without `--yes` it refuses. `--facts-only` calls no provider and shows no estimate.
- After each action, its actual tokens and cost are shown next to the estimate.

Prices are API list prices, in configuration with their sources, because they change. For a subscription, the dollars are what the same work would cost through the API; the real cost is the plan's usage, which the estimate shows as well.

### 15.5 Security
- The rules of sections 3 and 7.4 hold for every provider. What reaches a provider is what reached Claude before: prompts built from facts and guide pages, and `source/`.
- **Codex isn't confined to `source/`.** Its read-only sandbox prevents writes, not reads, and its tools are shell commands. Text in the target can make it read the target's excluded files through Codetrail's own data folder (`../mirror.git`, the guide, the database), read the reader's own files (keys, other repositories), and run the target's code inside the sandbox; whatever it reads goes to the provider as context before any scan. Codetrail therefore keeps Codex off unless a target opts in (`[assistant] allow_codex = true`), says so in `codetrail providers` and in every estimate that uses it, gives it an empty `HOME`, `/bin/sh` and no `USER`, switches off the reader's Codex configuration and the target's `AGENTS.md`, and scans its outputs. `claude_code` stays the default.
- **Every output is scanned for secrets** with gitleaks (Codetrail's configuration): pages, checks and digests before they are written to the guide, an answer before its final text is shown or saved, and grading feedback before it is stored. Claude Code's answers stream to the page as they are written, before the scan, since its reads are confined to `source/`; Codex's and local models' answers are buffered and shown only after it. A finding fails the page like a validation error, or replaces the answer or feedback with a notice, naming the rule only. The scan is a last check, not confinement.
- **`local` talks only to loopback.** The `base_url` is parsed and refused at startup unless its host is exactly `127.0.0.1`, `::1` or `localhost`, with no user information. The client ignores proxy settings and doesn't follow redirects. On a machine shared with other people, another account could listen on that port while the model server is down; `codetrail providers` shows the models the endpoint lists, so a stranger answering is noticed. Its file tools read at most `providers.local.max_read_bytes` per call, and Grep searches for plain text, so no pattern from the model can run away.
- **Prompts go in on stdin**, never in the command line, where other processes could read them. The settings, schema and read-log files a call needs are created with `mkstemp` outside `source/` and removed afterwards.
- **Processes are bounded:** each runs in its own process group, killed at `timeout_seconds` or on cancellation.
- **Programs are found safely:** each provider's program is resolved to an absolute path with relative `PATH` entries removed, and files in `source/` are never executable.
