# Phase 4: Generation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `codetrail update <target>` also writes the guide: Claude plans an outline from the facts, writes area and concept pages with checks, and a digest per update; Codetrail validates every page (documented quotes verified against their source, fact links and diagram placeholders checked) and commits the guide once. The page renders the guide, and an Update button runs the same update in the background.

**Architecture:** `claude` holds the interface Codetrail needs (`plan`, `write_page`, `write_digest`), the Agent SDK adapter, the fake, and the tool guard, a PreToolUse hook that refuses every tool but Read, Grep and Glob inside `source/` and logs each file read. `guide` reads and writes the knowledge base: Markdown with YAML front matter in its own git repository. `generate` builds the outline, finds affected pages, writes them within the budget, validates them, writes the digest, and commits.

**Tech Stack:** Claude Agent SDK (decision 9; the spike showed it runs under the founder's Claude Code sign-in), PyYAML (ADR 0004), markdown-it-py.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 4.3, 6, 7.1.

## Spike result
`claude-agent-sdk` 0.2.163 runs under the Claude Code sign-in with no API key. PreToolUse hooks see every Read, Grep and Glob call with absolute paths and can deny them; `can_use_tool` is not relied on, because read-only tools are often approved without it. `setting_sources=[]` loads no settings, hooks, skills or CLAUDE.md from the working directory. `max_budget_usd` bounds each call, and `output_format` gives structured output.

## Global Constraints
- Claude runs with `cwd=source/`, `tools=["Read", "Grep", "Glob"]` (none for grading later), `setting_sources=[]`, no MCP servers, `max_turns` and `max_budget_usd` from configuration, and the tool guard as a PreToolUse hook.
- The guard refuses any tool not in the list, and any path that doesn't resolve inside `source/`; it records every file Read opens.
- Prompts mark repository content as data, never instructions.
- Generated content is English. Diagrams are placeholders; Claude never writes Mermaid.
- A documented block's quote must appear, whitespace-normalized and contiguous, in the cited lines of `source/` or in the cited commit's message (scanned by gitleaks first). Otherwise the page fails validation.
- The guide repository lives in the data folder; it is committed once per update; uncommitted changes are discarded on failure, and an update refuses to start while the guide has uncommitted edits.
- Model names, budgets and limits come from configuration.

## Review Focus
1. Repository text telling Claude to read `../../.ssh/id_rsa`, call Bash or fetch a URL: the guard refuses, and the page still validates or fails cleanly — Task 2.
2. A documented quote that isn't in the cited lines (or cites an excluded or missing file) fails validation; a retry with the errors happens once; then the old page stays — Task 5.
3. A page body containing `<script>`, `javascript:` links or raw HTML renders inert — Task 7.
4. An update interrupted mid-generation leaves the guide's last commit intact and the next update rewrites the same pages — Task 6.
5. A second update with no fact changes calls Claude for nothing but the digest's skip — Task 6.

---

### Task 1: Settings and dependencies
`claude-agent-sdk` and `pyyaml` pinned. Target settings gain `[generation] max_pages_per_update = 20, concurrency = 2, max_turns = 30, max_budget_usd_per_call = 1.0` and `[models] plan, write, digest` (Opus 5.5 for planning, Sonnet 5.5 for writing and digests).

### Task 2: The tool guard and the Claude interface
`src/codetrail/claude/__init__.py`: request and draft types; `Claude` protocol with `plan(PlanRequest) -> PlanDraft`, `write_page(PageRequest) -> PageDraft`, `write_digest(DigestRequest) -> DigestDraft`; each draft carries `files_read` and `cost_usd`. `guard.py`: `ToolGuard(source_root)` with `decide(tool_name, tool_input) -> str | None` (a refusal reason, or None to allow) and `files_read`. `fake.py`: `FakeClaude` replays scripted drafts per method and records every request. `agent_sdk.py`: the adapter, building `ClaudeAgentOptions` as above, a JSON-schema `output_format` per method, and the guard as a PreToolUse hook. Tests: the guard's allow and refuse table (Read inside, Read `../`, absolute outside, a symlink pointing out, Grep without a path, Glob with an outside path, Bash, Write, WebFetch, an unknown tool); the adapter's options (no settings sources, only the three tools, the hook installed) built without calling Claude; a live test marked `live`.

### Task 3: The guide repository
`src/codetrail/guide/__init__.py`: `Page` (id, kind, title, front matter fields, body), `GuideRepository(path)` with `ensure()` (git init, Codetrail as committer), `read_page(id)`, `write_page(page)`, `pages()`, `has_uncommitted_changes()`, `commit(message)`, `discard()`, `read_outline()`, `write_outline()`; page ids match `(areas|concepts|digests|answers)/[a-z0-9][a-z0-9-]*`; front matter written with `yaml.safe_dump`, read with `yaml.safe_load`. Tests: round trip, unsafe ids refused, discard restores the last commit.

### Task 4: The outline and affected pages
`src/codetrail/generate/outline.py`: `Outline(pages: list[OutlineEntry])`, `OutlineEntry(id, kind, title, scope_paths, scope_kinds, facts)`, validation against the fact store and the manifest (unknown facts, empty scopes and duplicate ids refused, ids normalised). `scope.py`: `scope_hash(store, entry)`, the facts in a scope, and `is_affected(page, store, manifest) -> Reason | None` (new, facts changed, scope changed; "sources changed" is reported separately). Tests on a fixture store.

### Task 5: Page syntax and validation
`src/codetrail/generate/validate.py`: parses rationale blocks (`> [!documented] <path>#L<a>-L<b>` or `commit:<sha>`, then the quote; `> [!inferred]`), fact links `[[id]]` and diagram placeholders (`{{diagram imports scope=<folder>}}`, `{{diagram dependencies project=<id>}}`); `validate_page(body, checks, context) -> list[str]` returns problems. Quotes are checked against `source/` lines or the commit message from the mirror (gitleaks-scanned). Tests: a correct quote passes; a wrong quote, a wrong line range, an excluded file, an unknown fact, an unknown scope and a check grounded in an unknown fact each fail with a clear message.

### Task 6: Generation in the update
`src/codetrail/generate/__init__.py`: `generate_guide(context, claude) -> GenerationResult` — ensure the guide; refuse to start on uncommitted edits; plan the outline on the first run (and plan additions for facts in no scope); rank affected pages (most changed facts first); write up to the budget, `concurrency` at a time; validate, retry once with the problems, keep the old page on a second failure; write the digest (Claude when commits landed since the last digest's `to_commit`; none otherwise); commit once; on any exception discard and re-raise. `run_update` gains the guide step (skipped with `--facts-only`). Tests with the fake: first run plans and writes; a second run with no changes writes nothing; a changed fact rewrites exactly its pages; an invalid draft is retried, then kept old; an exception leaves the guide at its last commit.

### Task 7: Rendering the guide, and the Update button
`src/codetrail/web/render.py`: Markdown with markdown-it-py (`html` off, link validation on), rationale blocks rendered as labelled boxes ("Documented · <source link>", "Inferred"), fact links to `/facts/…`, diagram placeholders replaced by diagrams from facts. Routes: `/pages/{id:path}` (with a "sources changed" notice when a recorded file's blob changed), `/digests/{id}`; the home page lists the guide's areas, concepts and latest digest. `POST /update` starts the update in a background thread (one at a time; the target lock refuses a second), `GET /update/status` returns its state as JSON; page.js polls it. Tests: hostile Markdown inert; rationale and diagrams rendered; the button's endpoints behind the token.

### Task 8: Hamesh, documents, review
Run `codetrail update hamesh` with the real adapter (a small page budget), read the guide in the browser, check Hamesh unchanged; README, spec refinements; security review; finish.
