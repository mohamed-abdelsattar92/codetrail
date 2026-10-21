"""`codetrail mcp`: the tool server a coding agent starts, over a real subprocess's standard input and output."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codetrail.cli import main
from tests.fixtures.repos import make_repository

PYPROJECT = '[project]\nname = "api"\ndependencies = ["fastapi==0.120.0"]\n'


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    return tmp_path


def message(number: int | None, method: str, params: dict[str, object] | None = None) -> bytes:
    body: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if number is not None:
        body["id"] = number
    if params is not None:
        body["params"] = params
    return json.dumps(body).encode() + b"\n"


def test_mcp_over_a_real_subprocess(environment: Path) -> None:
    checkout = make_repository(environment / "target", [{"services/api/pyproject.toml": PYPROJECT}])
    assert main(["target", "add", "api", str(checkout)]) == 0
    assert main(["update", "api", "--facts-only"]) == 0
    lines = [
        message(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}),
        message(None, "notifications/initialized"),
        message(2, "tools/list"),
        message(3, "tools/call", {"name": "codetrail_map", "arguments": {}}),
    ]
    result = subprocess.run(
        [sys.executable, "-m", "codetrail", "mcp", "api"], input=b"".join(lines), capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    responses = [json.loads(text) for text in result.stdout.splitlines()]  # nothing but protocol messages
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert "api" in responses[0]["result"]["instructions"] and str(checkout) in responses[0]["result"]["instructions"]
    assert len(responses[1]["result"]["tools"]) == 4
    assert "# Map of api" in responses[2]["result"]["content"][0]["text"]


def test_mcp_for_an_unknown_target_fails_on_standard_error(environment: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "codetrail", "mcp", "nobody"], input=b"", capture_output=True, timeout=60
    )
    assert result.returncode == 1 and result.stdout == b"" and b"No target named 'nobody'" in result.stderr
