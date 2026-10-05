"""`codetrail providers`: each provider, whether it is installed and signed in, and how (design section 15.2)."""

import json
from pathlib import Path

import pytest

from codetrail.cli import main
from tests.fixtures.programs import make_program


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    return tmp_path


def test_providers_lists_each_one(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    claude = make_program(
        environment / "bin",
        "fake-claude",
        [json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"})],
    )
    config = environment / "config" / "codetrail" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'[providers.claude_code]\ncommand = "{claude.path}"\n[providers.codex]\ncommand = "/nonexistent/codex"\n'
        '[providers.local]\nbase_url = "http://127.0.0.1:9/v1"\n'
    )
    assert main(["providers"]) == 0
    output = capsys.readouterr().out
    assert f"claude_code  ready      Claude subscription (pro) · {claude.path}" in output
    assert "codex        not ready" in output and "npm install -g @openai/codex" in output
    assert "local        not ready" in output and "ollama serve" in output
    assert "Codex reads outside" in output
