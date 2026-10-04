# 0004. Render the page on the server with Jinja2 and markdown-it-py, keep front matter in YAML, serve with uvicorn, and vendor Mermaid

- Status: accepted (by the founder, 5 October 2026)
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code, from the design session of 5 October 2026
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 7.1, 7.4 and 12; brainstorm decisions 2 and 9

## Context
- Decision 9 chose FastAPI, Markdown files with front matter for the guide, and Mermaid rendered in the browser, but left open how the page is built.
- The page shows content that comes from the target repository and from Claude, so both are untrusted: rendering must make raw HTML, `javascript:` links and hostile diagram labels inert.
- The page has little interactivity: a streamed answer, diagrams, an update button.
- FastAPI's standard templating is Jinja2 (`fastapi.templating.Jinja2Templates`), and its standard server is uvicorn. Neither is named in decision 9.
- Codetrail is local by default: no CDN or third-party service.

## Decision drivers
1. Untrusted content rendered safely, with a strict CSP.
2. Standard tools and idioms (AGENTS.md, writing code rule 6).
3. One sanitizer, on the server.
4. No JavaScript toolchain.

## Options considered
### Option A: server-rendered with Jinja2, markdown-it-py and PyYAML; uvicorn; Mermaid vendored (chosen)
- Good, because Jinja2 autoescapes and its i18n extension uses the gettext catalogs of decision 7.
- Good, because markdown-it-py is CommonMark-compliant, can disable raw HTML, and validates links (dropping `javascript:`, `vbscript:`, `file:` and most `data:` URLs) by default.
- Good, because PyYAML's `safe_load` and `safe_dump` read and write front matter without constructing objects.
- Good, because a vendored Mermaid file, served from `'self'`, needs no CDN and fits the CSP.
- Bad, because it adds four Python dependencies (Jinja2 BSD-3, markdown-it-py MIT, PyYAML MIT, uvicorn BSD-3), and the vendored Mermaid (MIT) is updated by hand, not by Dependabot.

### Option B: a JavaScript front end (React, Svelte or similar) over a JSON API
- Good, because rich interactions are easier.
- Bad, because it adds Node to the runtime toolchain, a build step, and a second place where content must be sanitized.

### Option C: Python-Markdown and Mermaid from a CDN
- Good, because Python-Markdown is also widely used.
- Bad, because it isn't CommonMark-compliant, and safe rendering relies on extensions and configuration; a CDN breaks "local by default" and weakens the CSP.

## Decision
Option A. The page is rendered on the server by FastAPI with Jinja2 templates; Markdown by markdown-it-py with `html` off; front matter with PyYAML's safe functions; uvicorn serves it on `127.0.0.1`. Mermaid is vendored at a pinned version in `src/codetrail/web/static/vendor/`, with its version and licence recorded next to it, and initialized with `securityLevel: "strict"`.

## Consequences
- Easier: one rendering path, tested through FastAPI's test client; no JavaScript build.
- Harder: Mermaid updates are manual; the CSP needs `style-src 'unsafe-inline'` because Mermaid injects `<style>` into its SVGs.
- Revisit if the page's JavaScript grows past a single small file, which would also bring browser tests (design section 11).

## Changes required
- [ ] `pyproject.toml`: Jinja2, markdown-it-py, PyYAML and uvicorn in the runtime dependencies (Phase 3; PyYAML is also used by `guide` in Phase 4).
- [ ] `src/codetrail/web/static/vendor/`: Mermaid with a `VERSION` and `LICENSE` file (Phase 3).
- [x] The founder accepted this ADR on 5 October 2026.
