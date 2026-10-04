# 0001. Match exclusion rules with pathspec, in gitignore syntax

- Status: accepted (by the founder, 5 October 2026)
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code, from the design session of 5 October 2026
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 3.3 and 12; non-negotiable 1 in AGENTS.md

## Context
- The founder wants files excluded from everything Codetrail does "as if they're not there", through an ignore file with the syntax people already know from `.gitignore`: files, directories, wildcards, `**`, negation.
- These rules decide what Claude can read. A matcher that gets one edge case wrong (anchoring, a trailing `/`, `**` in the middle of a pattern, the order of negations) leaks a file the founder believed was excluded.
- Codetrail materializes the allowed files into `source/` from a bare mirror, so there is no working tree in which git's own matcher (`git check-ignore`) could run with the patterns installed.
- The standard library's `fnmatch` and `pathlib.PurePath.match` implement shell globs, not gitignore semantics.

## Decision drivers
1. Exactly gitignore's semantics, so a rule behaves the way the founder expects.
2. A small, auditable supply chain.
3. Matching in Python, without a working tree.

## Options considered
### Option A: `pathspec` with `GitIgnoreSpec` (chosen)
- Good, because it implements gitignore's matching, including the cases where git differs from the gitignore documentation, and is tested against them.
- Good, because it's pure Python with no dependencies, widely used (Black and yamllint use it), and licensed MPL-2.0.
- Bad, because it adds a dependency outside the stack in decision 9.

### Option B: a matcher written for Codetrail
- Good, because it adds no dependency.
- Bad, because it reimplements subtle, security-critical rules that a maintained library already gets right; the tests needed to trust it would be most of the work.

### Option C: `git check-ignore`
- Good, because it is git's own matcher.
- Bad, because it needs a working tree with the patterns installed as ignore files, which the bare mirror and materialized `source/` design avoids, and it spawns a process per batch of paths.

## Decision
Option A. `repo` matches the ignore files with `pathspec.GitIgnoreSpec`; the built-in secret patterns are matched the same way but kept in a separate spec that no negation can reach.

## Consequences
- Easier: the founder writes rules in a syntax they know; behaviour matches git's.
- Harder: one more dependency for Dependabot and the security review to watch.
- Revisit if `pathspec` stops being maintained or diverges from git's behaviour.

## Changes required
- [ ] `pyproject.toml`: `pathspec` in the runtime dependencies (Phase 1).
- [ ] Tests for the gitignore cases in design section 11, written before the matcher.
- [x] The founder accepted this ADR on 5 October 2026.
