@AGENTS.md

## Claude Code specifics
- Start Claude Code in this folder, the repository's root.
- The design is written in `docs/design/2026-10-05-codetrail-design.md`. Once the founder approves it, plan each phase with the `superpowers:writing-plans` skill, starting with Phase 0. Don't re-open decisions the spec or `docs/design/brainstorm-decisions.md` already record.
- Build test-first with the `superpowers:test-driven-development` skill.
- Until Phase 0 adds `.claude/` (permission rules, the push and secret-read hooks, the security-reviewer agent), nothing enforces the "Never do" rules in this repository; follow them exactly.
- If a push is ever needed, stop and tell the founder, who pushes from their own terminal.
