"""Tests for the push refusal in tools/git-hooks/agent-push-guard and the pre-push hook that runs it first."""
import os
import pathlib
import pty
import subprocess

import pytest

HOOKS = pathlib.Path(__file__).resolve().parents[2] / "tools" / "git-hooks"
AGENT_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODETRAIL_AGENT")
AGENT_SESSIONS = [
    {"CLAUDECODE": "1"},
    {"CLAUDE_CODE_ENTRYPOINT": "cli"},
    {"CODEX_SANDBOX": "seatbelt"},
    {"CODETRAIL_AGENT": "ci"},
]


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS and not k.startswith("CODEX_")}
    env.update(extra)
    return env


def run_with_terminal(script: str, env: dict[str, str]) -> int:
    """Run a hook script with stderr attached to a pseudo-terminal, as in the founder's shell."""
    leader, follower = pty.openpty()
    try:
        return subprocess.run(["sh", str(HOOKS / script)], env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=follower).returncode
    finally:
        os.close(leader)
        os.close(follower)


@pytest.mark.parametrize("marker", AGENT_SESSIONS)
def test_guard_refuses_agent_sessions(marker: dict[str, str]) -> None:
    assert run_with_terminal("agent-push-guard", clean_env(**marker)) == 1


def test_guard_refuses_sessions_without_a_terminal() -> None:
    result = subprocess.run(["sh", str(HOOKS / "agent-push-guard")], env=clean_env(),
                            stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert result.returncode == 1
    assert "without a terminal" in result.stderr


def test_guard_allows_the_founder_in_a_terminal() -> None:
    assert run_with_terminal("agent-push-guard", clean_env()) == 0


@pytest.mark.parametrize("marker", AGENT_SESSIONS)
def test_pre_push_refuses_agents_before_any_lefthook_setting(marker: dict[str, str]) -> None:
    assert run_with_terminal("pre-push", clean_env(LEFTHOOK="0", **marker)) == 1


def test_pre_push_runs_the_guard_first() -> None:
    lines = [line for line in (HOOKS / "pre-push").read_text().splitlines() if line and not line.startswith("#")]
    assert "agent-push-guard" in lines[0]
