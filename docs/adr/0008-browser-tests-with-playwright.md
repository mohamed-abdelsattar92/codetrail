# 0008. Test the page in a real browser with Playwright for Python, and check accessibility with axe-core

- Status: accepted (by the founder, 2026-10-05; recorded by Claude Code at the founder's request)
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder approved the page's redesign in the design session of 2026-10-05
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 11, 12, 14 and 16.7

## Context
Until now the page's JavaScript was one small file, and section 11 deferred browser tests until it grew, with Playwright named as the tool. The redesign adds a command palette, keyboard shortcuts, an Ask panel that keeps answers across pages, a theme switch and an outline that follows scrolling. That behaviour lives in the browser, and FastAPI's test client can't exercise it. Two rules make it worth testing there: no shortcut may spend anything, and single-key shortcuts must not fire while the reader types.

Facts (checked on 2026-10-05):
- Playwright for Python and its pytest plugin, pytest-playwright, are maintained by Microsoft under the Apache License 2.0. They drive Chromium, Firefox and WebKit headless; `playwright install chromium` downloads the browser into the user's cache, outside the repository (https://playwright.dev/python/).
- axe-core, by Deque, is the accessibility engine most tools use, under the Mozilla Public License 2.0. It runs inside the page and reports problems such as contrast, labels and roles (https://github.com/dequelabs/axe-core).

## Decision drivers
- The page's keyboard and panel behaviour is tested the way readers meet it.
- The tests use the project's language and test runner, pytest.
- Nothing is added to Codetrail's runtime.
- Accessibility, including contrast in both themes, is checked automatically.

## Options considered
### Option A: pytest-playwright with headless Chromium, and axe-core vendored for tests
- Good, because the tests are Python, run by pytest, and reuse the existing fixtures (a fixture repository, `serve`, the fake assistant).
- Good, because Playwright waits for the page by itself, which keeps the tests steady.
- Bad, because Chromium is about 150 MB to download, once per machine and per CI run; and the tests take seconds, not milliseconds, so they run in their own recipe.

### Option B: Selenium
- Bad, because it needs a separate driver for each browser and explicit waits, which make tests slower to write and more brittle.

### Option C: test the JavaScript modules in Node with a simulated DOM (jsdom with Vitest)
- Good, because it is fast.
- Bad, because it tests the modules apart from the real page, its security policy, its styles and its server, and adds a JavaScript test stack beside pytest.

### Option D: no browser tests
- Bad, because the palette, shortcuts and panel would be checked by hand only, and the rule that no key spends anything would have no test.

## Decision
Option A. `pytest-playwright` becomes a development dependency. The browser tests live in `tests/browser/`, run with headless Chromium against `serve` with the fake assistant, and run in `just test-browser`, which `just ci` and GitHub's CI include; CI installs Chromium with `playwright install --with-deps chromium`. axe-core's minified file is vendored for tests only, in `tests/browser/vendor/`, with its licence and a `VERSION` file recording its SHA-256; it is injected into the page under test and is never served by Codetrail.

## Consequences
- The palette, shortcuts, Ask panel, theme switch and outline have tests, and every page type gets an accessibility scan in both themes.
- Each machine that runs `just ci` downloads Chromium once; CI runs take a little longer.
- Codetrail's runtime is unchanged.
- Revisit if the browser tests become slow or flaky enough to be skipped, or if the page's JavaScript shrinks back to almost nothing.

## Changes required
- [x] Design: sections 11, 12, 14 and 16.7 (done in the design change that proposes this ADR).
- [x] `pyproject.toml` and `uv.lock`: `pytest-playwright` in the development group (Phase 11).
- [x] `justfile`: `test-browser`, included in `ci`; `setup` installs Chromium (Phase 11).
- [x] CI: install Chromium and run the browser tests (Phase 11).
- [x] `tests/browser/vendor/`: axe-core, its licence and `VERSION` (Phase 11).
