"""Tests for the permission rules in .claude/settings.json (AGENTS.md, Never do, rules 2 and 8)."""
import json
import pathlib

import pytest

SETTINGS = json.loads((pathlib.Path(__file__).resolve().parents[2] / ".claude" / "settings.json").read_text())
PERMISSIONS = SETTINGS["permissions"]

# Edit rules cover every tool that edits files (Write, MultiEdit, NotebookEdit), so the guardrails ask the founder
# before an agent changes them, at any depth: a worktree's copy too (security audit, low E8).
GUARDRAILS = [
    "./**/.claude/hooks/**",
    "./**/.claude/agents/**",
    "./**/.claude/settings.json",
    "./**/.claude/settings.local.json",
    "./**/.codex/**",
    "./**/tools/git-hooks/**",
    "./**/lefthook.yml",
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
