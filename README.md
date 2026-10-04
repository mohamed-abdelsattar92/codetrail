# Codetrail

Codetrail turns a git repository into a local learning guide for the person responsible for it. It tells you what changed and why it matters, and teaches the architecture, design patterns and tools behind it: concept pages tied to the real code, guided paths through it, checks on your understanding, and progress that notices when what you learned has gone stale. You read it as a local web page, and you can send questions from the page to Claude Code.

Diagrams are drawn from facts extracted from the code, so they can't show connections that aren't there. Explanations come from Claude, and rationale is marked as documented (quoted from a decision record or commit) or inferred.

The first repository it teaches is `hamesh-monorepo`; it is built to work on any repository.

## Status

Design in progress. Nothing is built yet.

- Decisions so far, and why: [docs/design/brainstorm-decisions.md](docs/design/brainstorm-decisions.md)
- Instructions for coding agents: [AGENTS.md](AGENTS.md)
