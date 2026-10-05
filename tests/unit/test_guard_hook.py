"""The tool guard as Claude Code's PreToolUse hook program: refuses outside `source/`, fails closed (design 15.1)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


def run_hook(source: Path, log: Path, payload: object) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, "-m", "codetrail.assistant.guard_hook", str(source), str(log)],
        input=text, capture_output=True, text=True, timeout=30, check=False,
    )  # fmt: skip


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print('hi')\n")
    return root


def denied(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 2:
        return True
    output = json.loads(result.stdout) if result.stdout.strip() else {}
    return bool(output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny")


def test_an_allowed_read_is_logged(source: Path, tmp_path: Path) -> None:
    log = tmp_path / "reads.log"
    result = run_hook(source, log, {"tool_name": "Read", "tool_input": {"file_path": str(source / "app/main.py")}})
    assert result.returncode == 0 and not denied(result)
    assert log.read_text().splitlines() == ["app/main.py"]


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
        {"tool_name": "Read", "tool_input": {"file_path": "../outside.txt"}},
        {"tool_name": "Bash", "tool_input": {"command": "cat /etc/hosts"}},
        {"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "/"}},
        {"tool_name": "Glob", "tool_input": {"pattern": "../**"}},
        {"tool_name": "Read", "tool_input": "not a mapping"},
        "not json at all",
    ],
)
def test_anything_outside_or_unreadable_is_refused(source: Path, tmp_path: Path, payload: object) -> None:
    log = tmp_path / "reads.log"
    assert denied(run_hook(source, log, payload))
    assert not log.exists() or log.read_text() == ""


def test_structured_output_is_allowed(source: Path, tmp_path: Path) -> None:
    result = run_hook(source, tmp_path / "reads.log", {"tool_name": "StructuredOutput", "tool_input": {"x": 1}})
    assert result.returncode == 0 and not denied(result)


def test_an_error_of_its_own_exits_with_code_2(source: Path, tmp_path: Path) -> None:
    unwritable = tmp_path / "missing-folder" / "reads.log"
    result = run_hook(source, unwritable, {"tool_name": "Read", "tool_input": {"file_path": "app/main.py"}})
    assert result.returncode == 2


def test_missing_arguments_exit_with_code_2() -> None:
    result = subprocess.run([sys.executable, "-m", "codetrail.assistant.guard_hook"], input="{}",
                            capture_output=True, text=True, timeout=30, check=False)  # fmt: skip
    assert result.returncode == 2
