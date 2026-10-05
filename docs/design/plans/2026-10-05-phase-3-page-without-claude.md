# Phase 3: The page, without Claude — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `codetrail serve <target>` serves a locked-down local page on `127.0.0.1`: a home page with the "you're behind" signal, area views with diagrams drawn from facts and rolled up above a node limit, fact and source views, a decisions list, and an interface that switches language, with English first and right-to-left support.

**Architecture:** `web` is a FastAPI application built by `create_app(paths, name, session)`. One middleware enforces the security model of design section 7.4 (Host, session cookie, Origin and token on writes, security headers). Pages are Jinja2 templates with autoescaping and gettext catalogs (ADR 0004, ADR 0005). Diagrams are Mermaid text built in Python from the fact store; the browser renders them with vendored Mermaid in strict mode. The signal reads the mirror only.

**Tech Stack:** FastAPI, uvicorn, Jinja2, markdown-it-py (used from Phase 4; added here with the page stack), Babel (development), Mermaid 11 vendored.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 4.4, 7.1, 7.2, 7.4, 8.5, 9.

## Global Constraints
- Bind `127.0.0.1` only; the host is not configurable.
- Every route but `/login` and `/static/*` requires the session cookie; every `POST` requires `Origin` equal to the served origin and `X-Codetrail-Token` equal to the session token (compared with `hmac.compare_digest`).
- Login codes and tokens come from `secrets.token_urlsafe(32)`; a code is single-use and expires after `server.login_code_ttl_seconds`.
- Headers on every response: the CSP of section 7.4, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`.
- No inline scripts; one `page.js`; Mermaid initialized with `securityLevel: "strict"`; node labels escaped.
- Source views serve only paths listed in `source.json`'s files, read from `source/`.
- Interface text comes from catalogs; English is the source and the fallback; the language code is validated against installed catalogs.
- Phase 3 areas are the top-level folders of the allowed files; Phase 4 replaces them with the outline.

## Review Focus
1. A request with `Host: evil.example` or `Host: 127.0.0.1.evil.example` is refused even with a valid session — Task 2.
2. A `POST` from a page on another origin, or with no `Origin`, is refused even with the cookie — Task 2.
3. A file name or module name containing `"]`, `<script>` or `%%{init: ...}%%` renders inert in a diagram and in HTML — Task 4.
4. `/source/../../etc/passwd`, `/source/.env` (excluded) and `/source/%2e%2e/x` return 404 without reading the file — Task 5.
5. The signal while an update holds the lock reports "updating" rather than fetching into the mirror — Task 6.

---

### Task 1: Settings and the page stack
Add `fastapi`, `uvicorn`, `jinja2`, `markdown-it-py` (runtime) and `babel` (dev), pinned. Global configuration gains `[server] port = 8765, login_code_ttl_seconds = 60`, `[ui] default_language = "en"`, `[signal] cache_seconds = 60`, `[diagrams] max_nodes = 60`. Migration `0002_settings.sql` creates `settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)`. Tests: defaults, validation (port range 1024–65535, positive limits).

### Task 2: The security layer
`src/codetrail/web/security.py`: `SessionState` (one login code with its expiry, the session id, the token), `issue_login_code() -> str`, `redeem(code) -> bool`, and a pure ASGI middleware applying, in order: Host allowlist (`127.0.0.1:<port>`, `localhost:<port>`), the session check (cookie `codetrail_session`), the Origin and token check on non-GET methods, and the headers on every response, refusals included. `GET /login?code=` redeems, sets the cookie (`HttpOnly`, `SameSite=Strict`, `Path=/`) and redirects to `/` with no code in the URL. Tests (FastAPI test client): each refusal and each header; a reused or expired code; a valid flow end to end.

### Task 3: Templates, catalogs and languages
`src/codetrail/web/i18n.py`: `installed_languages() -> dict[str, Language]` from `src/codetrail/locales/<code>/LC_MESSAGES/codetrail.mo` plus English always; `Language(code, name, direction, translations)`, where name and direction come from `pgettext("language name", "English")` and `pgettext("text direction", "ltr")`. Templates (`base.html` and pages) use `{% trans %}` and `gettext`; `<html lang dir>` follow the language; content blocks are `lang="en" dir="ltr"`. `POST /settings/language` validates the code and stores it in `settings`. Catalog tooling: `babel.cfg`, `just catalogs` (extract to `codetrail.pot`, update, compile) and `just catalogs-check` (extracted template current; every catalog has every message, matching placeholders and plural forms) run by `just ci`. A right-to-left test catalog lives in `tests/fixtures/locales/` and is used by a test through a locales-folder parameter.

### Task 4: Diagrams from facts
`src/codetrail/web/diagrams.py`: `imports_diagram(store, scope: str, max_nodes: int) -> Diagram` and `dependencies_diagram(store, project_id) -> Diagram`. `Diagram(mermaid: str, nodes: list[DiagramNode], rolled_up: bool)`; node ids are generated (`n1`…), labels escaped (quotes, angle brackets, `#`, `%`, backticks, brackets, line breaks) and every node links to its fact or folder. Above `max_nodes` modules roll up to their folders, one level at a time, with edge counts as labels. Tests: escaping of hostile names; roll-up keeps the node count at or under the limit; edges between folders are counted.

### Task 5: Pages
`src/codetrail/web/app.py` and `routes.py`: `/` (signal, areas, decisions count), `/areas/{name}` (files, modules and the diagram; a project's dependency diagram), `/facts/{id:path}` (attributes, sources with links, relations in and out), `/source/{path:path}` (only manifest paths, read from `source/`, shown with line numbers and an anchor per line), `/decisions` (number, title, status, date, link to source). Unknown names give a 404 page in the reader's language. Tests through the test client on a fixture target after an update.

### Task 6: The "you're behind" signal
`src/codetrail/repo/signal.py`: `behind(paths, name) -> Signal` with `merges`, `commits`, `areas` (top-level folders of visible changed paths), `head`, `updating: bool`; it takes the target lock without blocking, fetches into the mirror, compares the last snapshot's commit with the branch head (`merges_between`, `git diff --name-only`, filtered by the rules and the manifest's exclusions) and caches the result for `signal.cache_seconds`. Tests: no update yet; two merges touching `services/` and `.env` (excluded) report one area; locked → updating.

### Task 7: `codetrail serve`, page.js and Mermaid
`codetrail serve <target> [--no-browser]`: creates the session, prints the login URL, opens the browser (`webbrowser`) unless told not to, runs uvicorn on `127.0.0.1:<port>`. `static/page.js` initializes Mermaid (`startOnLoad: false`, `securityLevel: "strict"`), renders `.diagram` blocks, and sends the language form with the token header. Mermaid 11 is vendored at `static/vendor/mermaid.min.js` with `VERSION` and `LICENSE`. Smoke test: the app starts, `/static/page.js` is served with the CSP. Then the page is opened on Hamesh in the browser pane and checked (home, an area with its diagram, a fact, a source file, a refused excluded file, the RTL catalog's direction).

### Task 8: Documents, review, finish
README (Phase 3 done; `serve`), spec refinements, `just ci`, Hamesh read-only check, security review, finish.
