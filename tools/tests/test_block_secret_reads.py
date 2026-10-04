"""Tests for the Claude Code secrets guard, .claude/hooks/block_secret_reads.py (AGENTS.md, Never do, rule 2)."""
import json
import pathlib
import subprocess
import sys

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "block_secret_reads.py"


def run_hook(command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True).returncode


BLOCKED = [
    "cat apps/landing/.dev.vars",
    "cat .env",
    "less services/api/.env.local",
    "grep KEY apps/landing/.dev.vars",
    "base64 <apps/landing/.dev.vars",
    "cp apps/landing/.dev.vars /tmp/x",
    "docker run --env-file=services/api/.env image",
    "cat AuthKey_ABC123.p8",
    "openssl pkcs12 -in cert.p12",
    "head -c 100 ~/.ssh/id_ed25519",
    'cat "$HOME/.ssh/config"',
    "cat ~/.config/gh/hosts.yml",
    "cat ~/.config/gcloud/application_default_credentials.json",
    "cat ~/Library/Preferences/.wrangler/config/default.toml",
    "cat ~/.netrc",
    "cat ~/.npmrc",
    "cat infra/envs/dev/terraform.tfstate",
    "security find-generic-password -s wrangler -w",
    "security dump-keychain",
    "gcloud secrets versions access latest --secret=database-url",
    "echo $(cat .dev.vars)",
    "bash -c 'cat .env'",
    "cd apps/landing && cat .dev.vars",
    "tar c ~/.ssh",
    "grep -r . ~/.config/gh",
    "cat apps/landing/.dev.vars*",
    "cat apps/landing/.env*",
    "bash -lc 'cat .env'",
    "env sh -c 'cat .env'",
    "timeout 5 bash -c 'cat ~/.netrc'",
    "cat ~/.git-credentials",
    "cat ~/.terraform.d/credentials.tfrc.json",
    "ls ~/.aws && cat ~/.aws/config",
    "cat infra/envs/dev/terraform.tfvars",
    "cat prod.auto.tfvars.json",
    "cat ~/.pypirc",
    "cat certs/server.key",
    "cat backup.pfx",
    "cat putty.ppk",
    "cat id_rsa",
    "eval eval eval eval eval cat .env",
]

ALLOWED = [
    "cat apps/landing/.dev.vars.example",
    "cat services/api/.env.example",
    "cp .env.example .env.example.bak",
    "ls -la apps/landing",
    "test -f apps/landing/.env && echo present",
    "git check-ignore -v apps/landing/.env",
    "git commit -m 'docs: never print .dev.vars or a .pem file'",
    "git commit -q -F - <<'MSG'\ndocs: agents never cat .env or ~/.ssh/id_ed25519\nMSG",
    "mise exec -- just landing-worker-dev",
    "pnpm exec wrangler secret list",
    "grep -rn TODO apps/landing/src",
    "git diff develop -- .gitignore",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_reading_secrets(command: str) -> None:
    assert run_hook(command) == 2


@pytest.mark.parametrize("command", ALLOWED)
def test_allows_everyday_commands(command: str) -> None:
    assert run_hook(command) == 0


def test_fails_closed_when_its_import_fails(tmp_path: pathlib.Path) -> None:
    alone = tmp_path / "block_secret_reads.py"
    alone.write_text(HOOK.read_text())  # without block_push.py beside it, the import fails
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert subprocess.run([sys.executable, str(alone)], input=payload, capture_output=True, text=True).returncode == 2


def test_fails_closed_on_unreadable_input() -> None:
    result = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert result.returncode == 2


def test_ignores_other_tools() -> None:
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": ".env"}})
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)
    assert result.returncode == 0
