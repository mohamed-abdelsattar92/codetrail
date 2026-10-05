"""Checking each provider before paid work: installed, signed in, and signed in the way configured (design 15.2)."""

import json
from pathlib import Path

import httpx
import pytest

from codetrail.assistant import AssistantError
from codetrail.assistant.status import provider_status, require_ready
from codetrail.config import ClaudeCodeSettings, CodexSettings, GlobalConfig, ProvidersSettings, TargetConfig
from tests.fixtures.programs import make_program

ENVIRON = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/Users/reader",
    "USER": "reader",
    "LOGNAME": "reader",
    "ANTHROPIC_API_KEY": "key-1",
}
SUBSCRIBED = {
    "loggedIn": True,
    "authMethod": "claude.ai",
    "apiProvider": "firstParty",
    "subscriptionType": "max",
    "email": "reader@example.com",
    "orgName": "Reader",
}


def settings(claude: Path | None = None, codex: Path | None = None, auth: str = "subscription") -> GlobalConfig:
    providers = ProvidersSettings(
        claude_code=ClaudeCodeSettings(command=str(claude or "/nonexistent/claude"), auth=auth),  # type: ignore[arg-type]
        codex=CodexSettings(command=str(codex or "/nonexistent/codex"), auth=auth),  # type: ignore[arg-type]
    )
    return GlobalConfig(providers=providers)


def test_claude_signed_in_with_a_subscription_is_ready(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-claude", [json.dumps(SUBSCRIBED)])
    status = provider_status("claude_code", settings(claude=program.path), ENVIRON)
    assert status.ready and status.signed_in
    assert (status.program, status.method) == (str(program.path), "Claude subscription (max)")
    assert program.record["argv"] == ["auth", "status", "--json"]
    assert "ANTHROPIC_API_KEY" not in program.record["env"]
    assert "reader@example.com" not in repr(status)  # only what Codetrail needs is kept


def test_claude_signed_out_names_the_fix(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-claude", [json.dumps({"loggedIn": False, "authMethod": "none"})])
    status = provider_status("claude_code", settings(claude=program.path), ENVIRON)
    assert not status.ready and "/login" in status.fix


def test_claude_on_an_api_account_isnt_a_subscription(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-claude", [json.dumps({"loggedIn": True, "authMethod": "console"})])
    status = provider_status("claude_code", settings(claude=program.path), ENVIRON)
    assert not status.ready and 'auth = "api_key"' in status.fix
    assert provider_status("claude_code", settings(claude=program.path, auth="api_key"), ENVIRON).ready


def test_a_missing_program_names_how_to_install_it() -> None:
    status = provider_status("claude_code", settings(), ENVIRON)
    assert not status.ready and not status.installed and "Install Claude Code" in status.fix


def test_codex_signed_in_with_chatgpt_is_ready(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-codex", ["Logged in using ChatGPT"])
    status = provider_status("codex", settings(codex=program.path), ENVIRON)
    assert status.ready and status.method == "ChatGPT subscription"
    assert program.record["argv"] == ["login", "status"]
    assert program.record["env"]["SHELL"] == "/bin/sh"
    assert "reads outside" in status.warning


def test_codex_with_a_stored_api_key_isnt_a_subscription(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-codex", ["Logged in using an API key - sk-***"])
    status = provider_status("codex", settings(codex=program.path), ENVIRON)
    assert not status.ready and "codex login" in status.fix


def test_codex_signed_out(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-codex", ["Not logged in"], exit_code=1)
    assert not provider_status("codex", settings(codex=program.path), ENVIRON).ready


def test_a_local_endpoint_lists_its_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:11434/v1/models"
        return httpx.Response(200, json={"data": [{"id": "qwen3:14b"}, {"id": "llama3.2"}]})

    status = provider_status(
        "local", GlobalConfig(), ENVIRON, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert status.ready and status.models == ("qwen3:14b", "llama3.2")


def test_a_local_endpoint_thats_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    status = provider_status(
        "local", GlobalConfig(), ENVIRON, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert not status.ready and "ollama serve" in status.fix


def test_require_ready_checks_only_the_providers_the_calls_use(tmp_path: Path) -> None:
    program = make_program(tmp_path, "fake-claude", [json.dumps(SUBSCRIBED)])
    target = TargetConfig(repository=tmp_path, branch="main")
    assert [status.provider for status in require_ready(target, settings(claude=program.path), ENVIRON)] == [
        "claude_code"
    ]
    with pytest.raises(AssistantError, match="Install Claude Code"):
        require_ready(target, settings(), ENVIRON)
