# Phase 12: the whole system — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** one diagram of how a repository's parts fit together, grounded in facts, and facts for TypeScript, JavaScript and Astro code.

**Architecture:** two extractors (`typescript`, `github_actions`) and a Terraform addition produce facts as today; a system pass (`codetrail.system`) runs after them in every update and adds `part` facts and `implements`, `calls_via`, `deployed_on` and part-to-part `depends_on` relations with their evidence; a `system` diagram kind draws them on a new `/system` page, a home card, area pages and in answers.

**Tech stack:** Python, tree-sitter (TypeScript, TSX, JavaScript grammars), `json`, `tomllib`, PyYAML, Mermaid, FastAPI and Jinja2, pytest and Playwright.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, section 17 (and 4.4, 5, 7.3, 16 for what stays).

## Global constraints
- Facts only: every box and arrow comes from a fact; a matched arrow is dashed and records the rule and both names.
- Only committed, allowed files are read; no `vars` values, `env` values, `secrets.*` or step text in facts.
- Every diagram parses with the vendored Mermaid (the browser parse test covers each kind).
- Names that reach a prompt pass `LISTABLE`; labels pass `escape_label`.
- Generic: examples and fixtures use neutral names (`shop`); no test repository is named anywhere.
- ADR 0009 stays proposed until the founder decides.

## Review focus
- A repository with no parts at all: no system card, no system page link, no error.
- A file the TypeScript grammar can't parse, a 5 MB minified bundle, and a `.d.ts` file: a warning or a skip, never a failed update.
- A part folder name with spaces or Unicode: escaped in the diagram, left out of the answer's list.
- Two parts claiming the same file (a workspace package inside a project): the innermost part wins.
- A workflow whose step names a secret in its command line: the secret's name and value never reach a fact.

---

## Branch 1: `feature/typescript-extractor`
### Task 1: dependencies and ADR
**Files:** `pyproject.toml`, `uv.lock` (`tree-sitter-typescript==0.23.2`, `tree-sitter-javascript==0.25.0`).
### Task 2: the extractor
**Files:** `src/codetrail/extract/typescript.py`; `src/codetrail/facts/__init__.py` (`EntityKind.WORKER`); `config.py` (extractor name, default list); `update.py` (`build_extractors`); tests `tests/unit/test_typescript_extractor.py`.
**Interfaces:** `TypeScriptExtractor` (name `typescript`, version 1); projects `project:<folder>`, packages `package:npm/<name>`, modules `module:<path>` with `project`, routes `route:<folder> <METHOD> <path>` with `project`, workers `worker:<folder>` with `name`, `main`, `bindings` (list of `{type, name}`).
- [ ] Tests: imports (relative with extensions and `index`, `tsconfig` `paths`, bare to package, workspace to project, `import()`, `require`, re-exports, type-only imports); Astro code blocks and `<script>`; pages and endpoints with `[slug]` and `[...rest]`; `package.json` groups and workspaces (npm, pnpm, Yarn); Wrangler TOML, JSON and JSONC with every binding type and no `vars` values; skips for `*.d.ts`, `*.min.js`, `dist/`, `build/`, `.astro/`; unparseable files.
### Task 3: finish
- [ ] Design 5.2 table already updated; README extractors line; `just ci`; security review; merge develop; finish.

## Branch 2: `feature/deploy-evidence`
### Task 4: `github_actions`
**Files:** `src/codetrail/extract/github_actions.py`, `EntityKind.DEPLOYMENT`, config, `build_extractors`; test `tests/unit/test_github_actions_extractor.py`.
- [ ] Tests: each deploy form (commands and actions), target names, `working-directory` at step, job-defaults and root level, line numbers, and that no step text, `env` or `secrets.*` appears in any fact.
### Task 5: Terraform paths
**Files:** `src/codetrail/extract/terraform.py` (version 2); test `tests/unit/test_terraform_extractor.py`.
- [ ] Tests: `paths` from `source_dir`, `source`, `context`, `dockerfile`, `path`, `working_dir`, resolved against the module folder; non-path strings ignored.

## Branch 3: `feature/system-pass`
### Task 6: parts and connections
**Files:** `src/codetrail/system.py`; `facts/__init__.py` (`EntityKind.PART`; `RelationKind.IMPLEMENTS`, `CALLS_VIA`, `DEPLOYED_ON`); `update.py` (run the pass after the extractors, print rule counts); tests `tests/unit/test_system.py`, `tests/integration/test_system_update.py`.
**Interfaces:** `derive(entities, relations, files: Mapping[str, str], read: Callable[[str], bytes | None]) -> SystemFacts(entities, relations, warnings, counts)`.
- [ ] Tests: each part kind and its signal; innermost part; each connection rule explicit and matched; contract by path and by same blob; service implements, others call; no parts; hostile names; the pass never raises.

## Branch 4: `feature/system-diagram`
### Task 7: the diagram
**Files:** `web/diagrams.py` (`system_diagram(store, focus, max_nodes)`), `web/render.py`, `generate/validate.py`, `available_diagrams`, prompts; tests in `test_diagrams.py`, `test_validate.py`, `tests/browser/test_mermaid.py`.
### Task 8: the page
**Files:** `web/app.py` (`/system`, home card, area focus), templates `system.html`, `home.html`, `area.html`, sidebar, shortcuts (**G** then **Y**), CSS; tests `tests/api/test_system_page.py`, browser and accessibility scans.
### Task 9: end to end and docs
**Files:** `tests/e2e/test_whole_system.py` (the mixed fixture repository); README, getting-started, screenshots.
- [ ] `just ci`; security review; merge develop; finish; tell the founder.
