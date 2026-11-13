"""Tests for the permission rules in .claude/settings.json (AGENTS.md, Never do, rules 2 and 8)."""
import json
import pathlib

import pytest

SETTINGS = json.loads((pathlib.Path(__file__).resolve().parents[2] / ".claude" / "settings.json").read_text())
PERMISSIONS = SETTINGS["permissions"]

# Edit rules cover every tool that edits files (Write, MultiEdit, NotebookEdit), so the guardrails ask the founder
# before an agent changes them, at any depth: a worktree's copy too (security audit, low E8).
# Anchored at the file system's root (//), so a worktree session covers the main checkout's files too, and every
# guardrail the security checklist lists (branch review, M1 and L4).
GUARDRAILS = [
    "//**/AGENTS.md",
    "//**/CLAUDE.md",
    "//**/CLAUDE.local.md",
    "//**/.mcp.json",
    "//**/.claude/settings.json",
    "//**/.claude/settings.local.json",
    "//**/.claude/hooks/**",
    "//**/.claude/agents/**",
    "//**/.claude/commands/**",
    "//**/.claude/skills/**",
    "//**/.claude/rules/**",
    "//**/.codex/**",
    "//**/docs/security/**",
    "//**/.github/**",
    "//**/commitlint*.mjs",
    "//**/justfile",
    "//**/lefthook*.yml",
    "//**/.mise.toml",
    "//**/mise.toml",
    "//**/.mise.local.toml",
    "//**/mise.local.toml",
    "//**/.config/mise*",
    "//**/.config/mise/**",
    "//**/tools/git-hooks/**",
    "~/.claude/settings.json",
]


@pytest.mark.parametrize("path", GUARDRAILS)
def test_editing_a_guardrail_asks_the_founder(path: str) -> None:
    assert f"Edit({path})" in PERMISSIONS["ask"]


def test_every_env_file_but_the_examples_is_unreadable() -> None:
    # .env.ci, .env.backup and the like (security audit, low E9); the carve-out must follow the rule it narrows.
    deny = PERMISSIONS["deny"]
    assert deny.index("Read(./**/.env.*)") < deny.index("Read(!**/.env.example)")


def test_everything_codex_keeps_is_unreadable() -> None:
    # Its configuration, history and sessions can hold keys too, not only auth.json (security audit, low G2).
    assert "Read(~/.codex/**)" in PERMISSIONS["deny"]


def test_no_ask_rule_is_anchored_at_the_sessions_folder() -> None:
    assert all(rule.startswith(("Edit(//", "Edit(~/")) for rule in PERMISSIONS["ask"])
