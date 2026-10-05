"""The tool guard: Claude reads source/ only, with Read, Grep and Glob (design section 6.9)."""

from pathlib import Path

import pytest

from codetrail.claude.guard import ToolGuard


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("x = 1\n")
    (tmp_path / "outside.txt").write_text("not for Claude\n")
    (root / "link").symlink_to(tmp_path / "outside.txt")  # source/ never holds links; the guard copes anyway
    return root


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Read", {"file_path": "app/main.py"}),
        ("Read", {"file_path": "{root}/app/main.py"}),
        ("Grep", {"pattern": "x"}),
        ("Grep", {"pattern": "x", "path": "app"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"pattern": "*.py", "path": "{root}/app"}),
        ("StructuredOutput", {"answer": "anything"}),
    ],
)
def test_allows_reading_inside_source(source: Path, tool: str, arguments: dict[str, str]) -> None:
    guard = ToolGuard(source)
    resolved = {key: value.replace("{root}", str(source)) for key, value in arguments.items()}
    assert guard.decide(tool, resolved) is None


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Read", {"file_path": "../outside.txt"}),
        ("Read", {"file_path": "/etc/passwd"}),
        ("Read", {"file_path": "link"}),
        ("Read", {"file_path": "app/missing.py"}),
        ("Read", {}),
        ("Grep", {"pattern": "x", "path": "/"}),
        ("Grep", {"pattern": "x", "path": ".."}),
        ("Grep", {"pattern": "x", "glob": "../**"}),
        ("Glob", {"pattern": "../*"}),
        ("Glob", {"pattern": "/etc/*"}),
        ("Glob", {"pattern": "*", "path": "/Users"}),
        ("Bash", {"command": "cat /etc/passwd"}),
        ("Write", {"file_path": "app/main.py", "content": "x"}),
        ("Edit", {"file_path": "app/main.py"}),
        ("WebFetch", {"url": "https://example.com"}),
        ("Task", {"prompt": "x"}),
        ("mcp__anything__tool", {}),
        ("Glob", {"pattern": "{..,x}/*"}),
        ("Glob", {"pattern": "**/{/etc,x}/*"}),
        ("Grep", {"pattern": "x", "glob": "{..}/*"}),
        ("Glob", {"pattern": "a\\..\\b"}),
        ("Read", {"file_path": "~nosuchuser/x"}),
        ("Read", {"file_path": "a\x00b"}),
    ],
)
def test_refuses_everything_else(source: Path, tool: str, arguments: dict[str, str]) -> None:
    assert ToolGuard(source).decide(tool, arguments) is not None


def test_records_the_files_read(source: Path) -> None:
    guard = ToolGuard(source)
    guard.decide("Read", {"file_path": str(source / "app" / "main.py")})
    guard.decide("Read", {"file_path": "app/main.py"})
    guard.decide("Read", {"file_path": "../outside.txt"})
    assert guard.files_read == ["app/main.py"]


@pytest.mark.anyio
async def test_the_hook_denies_with_a_reason(source: Path) -> None:
    guard = ToolGuard(source)
    denied = await guard.hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, None, {})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = await guard.hook({"tool_name": "Read", "tool_input": {"file_path": "app/main.py"}}, None, {})
    assert allowed == {}


@pytest.mark.anyio
async def test_the_hook_fails_closed_on_strange_input(source: Path) -> None:
    guard = ToolGuard(source)
    denied = await guard.hook({"tool_name": "Read", "tool_input": ["not", "a", "mapping"]}, None, {})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
