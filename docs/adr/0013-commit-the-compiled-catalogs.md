# 0013. Use Babel at development time for the interface's catalogs, and commit the compiled catalogs

- Status: proposed
- Date: 2026-10-09
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder chose on 2026-10-09 to add the Arabic interface and to ship its compiled catalog with Codetrail
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 7.2 and 11; brainstorm decision 7; supersedes [ADR 0005](0005-babel-for-interface-catalogs.md), whose choice of Babel it keeps

## Context
- ADR 0005 chose Babel, at development time only, to extract the interface's strings and compile each language's `.po` into the `.mo` file the standard library's `gettext` reads. It decided that compiled `.mo` files "are generated and never committed", and named its own trigger: "Revisit if catalogs must ship precompiled inside a released package."
- That trigger has come: Arabic is the first real catalog. Codetrail lists the languages whose compiled catalogs it finds (`codetrail.web.i18n.installed_languages`), and none is compiled for a reader:
  - `.gitignore` ignores `*.mo`, so no clone and no release tag carries one;
  - `uv tool install git+https://github.com/mohamed-abdelsattar92/codetrail@<tag>`, the documented way to install a release, builds the package from the tagged tree, without `just`;
  - `uv tool install .` from a clone packages what is in the folder, and `just setup` doesn't run `just catalogs`, so a clone has no `.mo` either unless its owner ran that recipe.
  A reader would see English only, and Arabic would be missing from the picker, silently.
- The build backend, `uv_build`, has no build hooks, so it can't compile catalogs while it builds.
- A `.mo` file is small (the Arabic catalog's 291 messages compile to 31 KB) and `pybabel compile` writes the same bytes from the same `.po` and Babel version, so a test can check that a committed `.mo` matches its source (checked on 2026-10-09 with Babel 2.18.0).
- `just catalogs` had never run on a real catalog, and its update step couldn't: it passed `--no-location`, which `pybabel update` rejects, and `--omit-header`, which drops the header that names a catalog's plural forms (six for Arabic); `pybabel compile` skips a catalog without a header as fuzzy. Without `--ignore-pot-creation-date`, every run would also rewrite the catalog's header date, and with it the compiled file.

## Decision drivers
1. A reader who installs Codetrail in the documented ways gets every language it ships, with nothing to run.
2. Nothing added to Codetrail's runtime, and no new tool.
3. A compiled catalog can't drift from its source without CI failing.
4. Adding a language stays one catalog on one branch, with no code change (design goal 6).

## Options considered
### Option A: keep Babel at development time and commit each compiled `.mo` next to its `.po`, checked by a test (chosen)
- Good, because every install path gets the catalogs: they are in the tree that `uv` builds from.
- Good, because nothing changes at runtime, and no tool or build step is added.
- Good, because a test compiles each `.po` the way `just catalogs` does and fails when the committed `.mo` differs, or is missing, so CI catches a forgotten compile.
- Bad, because a generated binary file is committed, and every catalog change touches two files; a merge that conflicts in a `.mo` is resolved by running `just catalogs`, not by hand.

### Option B: switch the build backend to one with build hooks (hatchling) and compile while building
- Good, because only sources are committed.
- Bad, because it changes the toolchain (a dependency outside the stack in decision 11) for one step, and the build would need Babel, so Babel would become a build dependency of every install.

### Option C: read the `.po` files at runtime
- Good, because nothing compiled is committed or built.
- Bad, because the standard library can't read `.po` files: it needs Babel at runtime (a new runtime dependency) or a parser of our own, which ADR 0005 already rejected (its option C).

### Option D: keep ADR 0005 and tell readers to run `just catalogs` after installing
- Bad, because a reader who installs a release has no `just`, no Babel and no clone, and the failure is silent: the language just isn't offered.

## Decision
Option A. Babel stays a development dependency, used through `just catalogs` to extract the template, update each language's `.po` and compile its `.mo`. The compiled `src/codetrail/locales/<code>/LC_MESSAGES/codetrail.mo` files are committed with their `.po` files. A test fails when a catalog has no compiled `.mo`, when a `.mo` has no `.po`, when a catalog or one of its messages is marked fuzzy (which `pybabel compile` would skip), or when a committed `.mo` differs from what its `.po` compiles to. Catalogs keep their gettext header. This ADR supersedes ADR 0005, whose choice of Babel it repeats.

## Consequences
- Easier: every install path, including `uv tool install git+…@<tag>`, gets every shipped language; `just setup` needs no new step.
- Harder: a catalog change means running `just catalogs` and committing the `.po` and the `.mo` together; the test says so when one is forgotten. A conflict in a `.mo` is resolved by taking either side and running `just catalogs`.
- Revisit if the build backend gains build hooks Codetrail would use anyway, or if a catalog grows past a few hundred kilobytes.

## Changes required
- [ ] `.gitignore`: stop ignoring the compiled catalogs under `src/codetrail/locales/` (this change).
- [ ] `justfile`: `just catalogs` updates each catalog keeping its header and its date, so it runs on real catalogs and a second run changes nothing (this change).
- [ ] `tests/unit/test_catalogs.py`: every catalog has a compiled `.mo` that matches it, and every `.mo` has a catalog (this change).
- [ ] Design section 7.2: compiled `.mo` files are committed and checked, citing this ADR; section 12's dependency table cites it for Babel (this change).
- [ ] README, Languages: adding a language is `pybabel init`, translating, then `just catalogs`, committing the `.po` and the `.mo` together (this change).
- [ ] `docs/adr/README.md`: this ADR in the index (this change).
- [ ] When the founder accepts this ADR: ADR 0005's status becomes `superseded by 0013`.
