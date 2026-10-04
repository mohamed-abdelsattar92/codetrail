# 0005. Use Babel at development time to extract and compile the interface's gettext catalogs

- Status: accepted (by the founder, 5 October 2026)
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code, from the design session of 5 October 2026
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 7.2 and 11; brainstorm decision 7 (revised 5 October 2026)

## Context
- Decision 7 makes the interface English-first and switchable, with one gettext catalog (`.po`) per language, read at runtime by the standard library's `gettext`. Languages will be added over time as files.
- Catalogs need tooling around them: extracting translatable strings from Python and Jinja2 templates into a template (`.pot`), updating each language's `.po` from it, and compiling `.po` files into the `.mo` files `gettext` reads.
- The standard library reads `.mo` files but ships no supported extractor or compiler; GNU gettext's tools don't understand Jinja2 templates.

## Decision drivers
1. Standard formats and tools that translators and their tools understand.
2. Extraction from Jinja2 templates as well as Python.
3. Nothing added to Codetrail's runtime.

## Options considered
### Option A: Babel as a development dependency (chosen)
- Good, because `pybabel extract`, `update` and `compile` are the standard Python workflow, and Jinja2 provides Babel's extractor for its templates.
- Good, because Babel stays out of the runtime: Codetrail reads compiled catalogs with the standard library.
- Bad, because it adds a development dependency (BSD-3) outside the stack.

### Option B: GNU gettext tools (`xgettext`, `msgmerge`, `msgfmt`)
- Good, because they are the reference implementation.
- Bad, because `xgettext` can't read Jinja2 templates, and they are another system tool to pin with mise.

### Option C: compile catalogs at runtime with a small parser
- Good, because it needs no tooling.
- Bad, because it is code to write and maintain, and extraction would still need a tool.

## Decision
Option A. Babel is a development dependency. `just` recipes extract strings to `src/codetrail/locales/codetrail.pot`, update each language's `.po`, and compile `.mo` files, which are generated and never committed. CI fails if the extracted template is out of date.

## Consequences
- Easier: adding a language is `pybabel init` and translating the new `.po` file.
- Harder: `just setup` must compile catalogs before the page runs, including for `uv tool install` users; the install instructions say so.
- Revisit if catalogs must ship precompiled inside a released package.

## Changes required
- [ ] `pyproject.toml`: Babel in the development dependency group (Phase 3).
- [ ] `justfile`: recipes to extract, update and compile catalogs (Phase 3).
- [ ] `.gitignore`: `*.mo` (Phase 3).
- [x] The founder accepted this ADR on 5 October 2026.
