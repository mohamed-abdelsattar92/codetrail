# Codetrail

Codetrail turns a git repository into a local learning guide for the person responsible for it. It tells you what changed and why it matters, and teaches the architecture, design patterns and tools behind it: concept pages tied to the real code, guided paths through it, checks on your understanding, and progress that notices when what you learned has gone stale. You read it as a local web page, and you can send questions from the page to Claude Code.

Diagrams are drawn from facts extracted from the code, so they can't show connections that aren't there. Explanations come from Claude, and rationale is marked as documented (quoted from a decision record or commit) or inferred.

The interface is in English and can switch to other languages, which are added over time as translation files; Claude answers questions in the language you choose.

The first repository it teaches is `hamesh-monorepo`; it is built to work on any repository.

## Status

Design written, awaiting the founder's review. Nothing is built yet. It is built in eight phases; the first, Phase 0, sets up the same engineering tooling as `hamesh-monorepo`: mise, `just`, uv, lefthook git hooks, commitlint, git-flow, ruff, mypy, pytest and test-driven development.

- The design: [docs/design/2026-10-05-codetrail-design.md](docs/design/2026-10-05-codetrail-design.md)
- Decisions from the brainstorm, and why: [docs/design/brainstorm-decisions.md](docs/design/brainstorm-decisions.md)
- Architecture decision records, five of them proposed: [docs/adr/](docs/adr/README.md)
- Instructions for coding agents: [AGENTS.md](AGENTS.md)
