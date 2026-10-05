"""The environment provider programs get: an allowlist, never a key in subscription mode (design section 15.2)."""

from pathlib import Path

import pytest

from codetrail.assistant import AssistantError
from codetrail.assistant.environment import child_environment, resolve_program, safe_path

PARENT = {
    "PATH": "/usr/bin:.:bin:/opt/tools/bin::",
    "HOME": "/Users/reader",
    "USER": "reader",
    "LOGNAME": "reader",
    "SHELL": "/bin/zsh",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "TMPDIR": "/var/folders/reader",
    "TERM": "xterm",
    "ANTHROPIC_API_KEY": "key-1",
    "ANTHROPIC_AUTH_TOKEN": "token-1",
    "CLAUDE_CODE_OAUTH_TOKEN": "token-2",
    "OPENAI_API_KEY": "key-2",
    "CODEX_API_KEY": "key-3",
    "AWS_SECRET_ACCESS_KEY": "key-4",
    "GITHUB_TOKEN": "token-3",
    "MY_TOKEN": "token-4",
    "CLAUDE_CONFIG_DIR": "/Users/reader/.claude-work",
}


def test_claude_in_subscription_mode_gets_the_allowlist_and_no_key() -> None:
    environment = child_environment("claude_code", "subscription", PARENT)
    assert environment == {
        "PATH": "/usr/bin:/opt/tools/bin",
        "HOME": "/Users/reader",
        "USER": "reader",
        "LOGNAME": "reader",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": "/var/folders/reader",
        "TERM": "xterm",
        "CLAUDE_CONFIG_DIR": "/Users/reader/.claude-work",
    }


def test_claude_with_an_api_key_passes_only_its_key_variable() -> None:
    environment = child_environment("claude_code", "api_key", PARENT)
    assert environment["ANTHROPIC_API_KEY"] == "key-1"
    assert not {"ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "GITHUB_TOKEN"} & set(environment)


def test_codex_gets_an_empty_home_a_plain_shell_and_its_own_folder(tmp_path: Path) -> None:
    environment = child_environment("codex", "subscription", PARENT, empty_home=tmp_path)
    assert environment == {
        "PATH": "/usr/bin:/opt/tools/bin",
        "HOME": str(tmp_path),
        "SHELL": "/bin/sh",
        "CODEX_HOME": "/Users/reader/.codex",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": "/var/folders/reader",
        "TERM": "xterm",
    }


def test_codex_keeps_a_configured_codex_home_and_its_keys_only_by_choice(tmp_path: Path) -> None:
    parent = PARENT | {"CODEX_HOME": "/Users/reader/codex-home"}
    environment = child_environment("codex", "api_key", parent, empty_home=tmp_path)
    assert environment["CODEX_HOME"] == "/Users/reader/codex-home"
    assert (environment["OPENAI_API_KEY"], environment["CODEX_API_KEY"]) == ("key-2", "key-3")
    assert "ANTHROPIC_API_KEY" not in environment


def test_a_relative_codex_home_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AssistantError, match="CODEX_HOME"):
        child_environment("codex", "subscription", PARENT | {"CODEX_HOME": "codex"}, empty_home=tmp_path)


def test_relative_and_empty_path_entries_are_dropped() -> None:
    assert safe_path(".:bin:/usr/bin::./x:/bin") == "/usr/bin:/bin"


def test_programs_resolve_to_absolute_paths_only(tmp_path: Path) -> None:
    program = tmp_path / "bin" / "claude"
    program.parent.mkdir()
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    assert resolve_program("claude", f"relative:{program.parent}") == str(program)
    assert resolve_program(str(program), "") == str(program)
    with pytest.raises(AssistantError, match="isn't installed"):
        resolve_program("claude", "relative")
    with pytest.raises(AssistantError, match="absolute"):
        resolve_program("bin/claude", f"{program.parent}")
