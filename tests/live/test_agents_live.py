"""The real Claude Code and Codex start Codetrail's tool server and call it (`just test-live`; CI skips it).

Each agent runs only where it is installed and signed in; the calls use the cheapest model and a small budget.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from codetrail.assistant.status import provider_status
from codetrail.cli import main
from codetrail.config import GlobalConfig
from tests.fixtures.repos import make_repository

pytestmark = pytest.mark.live
CHEAP = "claude-haiku-4-5-20251001"
PROMPT = "Call the codetrail_map tool, then reply with the first line it returned and nothing else."


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    folders = {variable: str(tmp_path / folder) for variable, folder in
               (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"))}  # fmt: skip
    for variable, folder in folders.items():
        monkeypatch.setenv(variable, folder)
    checkout = make_repository(tmp_path / "tiny", [{"app/pyproject.toml": '[project]\nname = "tiny"\n'}])
    assert main(["target", "add", "tiny", str(checkout)]) == 0
    assert main(["update", "tiny", "--facts-only"]) == 0
    return folders


def test_claude_code_calls_the_tool_server(environment: dict[str, str], tmp_path: Path) -> None:
    if not provider_status("claude_code", GlobalConfig()).ready:
        pytest.skip("Claude Code isn't installed or signed in with a subscription here.")
    config = tmp_path / "mcp.json"
    server = {"command": sys.executable, "args": ["-I", "-m", "codetrail", "mcp", "tiny"], "env": environment}
    config.write_text(json.dumps({"mcpServers": {"codetrail-tiny": server}}))
    result = subprocess.run(
        [str(shutil.which("claude")), "-p", PROMPT, "--model", CHEAP, "--mcp-config", str(config),
         "--strict-mcp-config", "--allowedTools", "mcp__codetrail-tiny__codetrail_map", "--setting-sources", "",
         "--max-budget-usd", "0.2"],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "Map of tiny" in result.stdout


def test_codex_calls_the_tool_server(environment: dict[str, str], tmp_path: Path) -> None:
    if not provider_status("codex", GlobalConfig()).ready:
        pytest.skip("Codex isn't installed or signed in here.")
    arguments = json.dumps(["-I", "-m", "codetrail", "mcp", "tiny"])
    variables = "{" + ", ".join(f"{key} = {json.dumps(value)}" for key, value in environment.items()) + "}"
    result = subprocess.run(
        [str(shutil.which("codex")), "exec", PROMPT, "--skip-git-repo-check", "--sandbox", "read-only",
         "-c", f"mcp_servers.codetrail-tiny.command={json.dumps(sys.executable)}",
         "-c", f"mcp_servers.codetrail-tiny.args={arguments}",
         "-c", f"mcp_servers.codetrail-tiny.env={variables}"],
        capture_output=True, text=True, timeout=300, cwd=tmp_path,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "Map of tiny" in result.stdout
