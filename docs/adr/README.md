# Architecture decision records

One file per decision that is hard to reverse. The format is MADR (Markdown Architectural Decision Records), trimmed; copy `template.md` to start one.

## When a decision needs an ADR
Write one when a decision is hard to reverse or touches any of these:
- a dependency outside the stack in `docs/design/brainstorm-decisions.md` (decision 9) or the engineering setup (decision 11);
- security: what Codetrail reads, what reaches Claude, what the page and bridge accept;
- an external service (AGENTS.md allows none but Claude without an ADR);
- storage formats that are costly to migrate: the fact store, the guide's layout and front matter;
- the toolchain or repository layout;
- a deviation from the design in `docs/design/`.

Everything smaller is explained in the commit body, not in an ADR.

## Rules
- File name `NNNN-kebab-case-title.md`, four digits, numbers never reused.
- Status is one of `proposed`, `accepted`, `rejected`, `deprecated`, `superseded by NNNN`.
- Coding agents (Claude Code, Codex) may write ADRs only with status `proposed`. The founder changes the status to `accepted` or `rejected`, in the same change or a later one.
- An accepted ADR is never rewritten. To change a decision, write a new ADR and set the old one's status to `superseded by NNNN`; that status line is the only edit allowed on the old file.
- Every ADR names the design sections it affects, and the documents it requires to change. The change that accepts it also makes those changes.
- Accepted ADRs override the design documents where they conflict.

## Index
| Number | Title | Status | Date |
|---|---|---|---|
| [0001](0001-pathspec-for-exclusion-rules.md) | Match exclusion rules with pathspec, in gitignore syntax | accepted | 2026-10-05 |
| [0002](0002-gitleaks-on-every-update.md) | Scan the materialized sources with gitleaks on every update, and exclude what it flags | accepted | 2026-10-05 |
| [0003](0003-facts-with-validity-ranges.md) | Store each fact once with a validity range over snapshots | accepted | 2026-10-05 |
| [0004](0004-server-rendered-page-stack.md) | Render the page on the server with Jinja2 and markdown-it-py, keep front matter in YAML, serve with uvicorn, and vendor Mermaid | accepted | 2026-10-05 |
| [0005](0005-babel-for-interface-catalogs.md) | Use Babel at development time to extract and compile the interface's gettext catalogs | accepted | 2026-10-05 |
| [0006](0006-assistant-providers-and-subscriptions.md) | Run the reader's own assistant programs (Claude Code, Codex) or a local model, on their subscription | proposed | 2026-10-05 |
| [0007](0007-bundle-the-inter-typeface.md) | Bundle the Inter typeface with the page | accepted | 2026-10-05 |
| [0008](0008-browser-tests-with-playwright.md) | Test the page in a real browser with Playwright for Python, and check accessibility with axe-core | accepted | 2026-10-05 |
| [0009](0009-typescript-and-javascript-grammars.md) | Parse TypeScript, JavaScript and Astro with tree-sitter's TypeScript and JavaScript grammars | proposed | 2026-10-05 |
| [0010](0010-agpl-with-a-commercial-license.md) | License Codetrail under the AGPL 3.0 only, sell commercial licenses, and reserve the name and logo | proposed | 2026-10-07 |
| [0011](0011-parallel-quick-tests-with-pytest-xdist.md) | Run the pre-push hook's quick tests in parallel with pytest-xdist, on the unit, API and hook tests only | proposed | 2026-10-07 |
