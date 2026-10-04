"""Tests for the Claude Code push guard, .claude/hooks/block_push.py (AGENTS.md, Never do, rule 1)."""
import json
import pathlib
import subprocess
import sys

import pytest

HOOK = pathlib.Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "block_push.py"


def run_hook(command: str) -> int:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True).returncode


BLOCKED = [
    "git {push,--no-verify} origin develop",
    "{git,push} origin develop",
    "git push",
    "git push origin develop",
    "git push --force origin main",
    "git push --dry-run",
    "git -C /tmp/r push origin develop",
    "env A=1 git push",
    "cd repo && git push",
    "bash -c 'git push'",
    "eval git push",
    "echo $(git push)",
    "git send-pack origin",
    "git subtree push --prefix x origin y",
    "git remote set-url origin git@example.com:x/y.git",
    "git remote add other https://example.com/x.git",
    "git config alias.p push",
    "git credential fill",
    "gh pr create --fill",
    "gh pr merge 1",
    "gh repo sync",
    "gh auth token",
    "gh api repos/x/y/pulls -f title=t",
    "gh api -X POST repos/x/y/issues",
    "git flow feature publish x",
    "git-flow feature publish x",
    "git flow publish",
    "git flow feature finish x",
    "git flow feature finish x --no-push",
    "git flow feature finish x --keepremote",
    "git flow feature finish x --no-ff --no-push --keepremote --push",
    "git flow feature finish x --no-push --keepremote --no-keepremote",
    "git flow feature finish x --no-push --keepremote --pushtag",
    "git flow feature finish x --no-push --keep --no-keep",
    "git -C /tmp/r flow feature finish x",
    "git flow release finish 1.0 --no-push --keepremote",
    "git flow hotfix finish h --no-push --keepremote",
    "git flow feature delete x",
    "git flow feature delete x --remote",
    "bash -c 'git flow feature publish x'",
    "git config gitflow.feature.finish.push true",
    "git config --local gitflow.feature.finish.keepremote false",
    "git config gitflow.branch.feature.deleteRemote true",
    # skipping the git hooks (rule 8)
    "git commit --no-verify -m 'docs: x'",
    "git commit -n -m 'docs: x'",
    "git commit -anm 'docs: x'",
    "git merge --no-verify feature/x",
    "LEFTHOOK=0 git commit -m 'docs: x'",
    "env LEFTHOOK=0 git commit -m 'docs: x'",
    "LEFTHOOK_EXCLUDE=secrets git commit -m 'docs: x'",
    "export LEFTHOOK=0",
    "git -c core.hooksPath=/dev/null commit -m 'docs: x'",
    "git config core.hooksPath /tmp/none",
    "git flow feature finish x --no-ff --no-push --keepremote --no-fetch --no-verify",
    # commands the guard can't read are refused, not let through
    "git flow release finish 1.0 --no-push --keepremote -M $'x\\'y'",
    "git commit --no-verify -m $'it\\'s'",
    "echo 'unclosed",
    "cat <<EOF\ngit push",
    # heredoc tricks: only real heredocs are skipped, and unquoted bodies are checked
    "cat <<EOF\n$(git commit --no-verify -am x)\nEOF",
    "cat <<'EOF'\n  EOF\n: <<'X'\nEOF\ngit push\nX",
    "cat <<<EOF\ngit push",
    "echo \"<<X\"\ngit push\nX",
    "echo hi # <<EOF\ngit push\nEOF",
    # a newline separates commands; a backslash continues the line
    "echo hi\ngit push",
    "git \\\npush origin develop",
    # moving refs by hand (origin/develop must stay what the founder pushed)
    "git update-ref refs/remotes/origin/develop develop",
    "git fetch . develop:refs/remotes/origin/develop",
    "git fetch origin +refs/heads/develop:refs/remotes/origin/develop2",
]

ALLOWED = [
    "git status",
    "git log --oneline -5",
    "git commit -m 'docs: x'",
    "git fetch --prune -q origin",
    "git branch -d feature/x",
    "git merge --no-ff feature/x",
    "git config --get-regexp gitflow",
    "git flow feature start x",
    "git flow feature finish x --no-ff --no-push --keepremote --no-fetch",
    "git flow feature finish x --no-ff --no-push --keep --no-fetch -M 'Merge x'",
    "git flow feature finish --abort",
    "git flow config sync",
    "git flow init --preset=classic --shared",
    "gh run list",
    "gh api repos/x/y/pulls",
    "git commit -m 'docs: mention -n and --no-verify in prose'",
    "git commit -m 'feat(api): new endpoint'",
    "LEFTHOOK_VERBOSE=1 git commit -m 'docs: x'",
    "git log -n 5",
    "git commit -q -F - <<'MSG'\ndocs: mention git push and git flow feature publish in prose\nMSG",
    # the finish command AGENTS.md documents, with backticks, $ and quotes in a finding's title
    "git flow feature finish x --no-ff --no-push --keepremote --no-fetch -M \"$(cat <<'EOF'\n"
    "Merge branch 'feature/x' into develop\n\nSecurity review: pass (reviewed abc1234)\n"
    "- Low: `.dev.vars` isn't denied; $(whoami) and \"quotes\"\nEOF\n)\"",
    "cat <<-'EOF'\n\tindented body with git push in prose\n\tEOF",
    "cat <<EOF\nplain text $HOME\nEOF",
    "git fetch origin",
    "git fetch --prune origin develop",
    "grep -c '<<' notes.txt",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_push_forms(command: str) -> None:
    assert run_hook(command) == 2


@pytest.mark.parametrize("command", ALLOWED)
def test_allows_everyday_commands(command: str) -> None:
    assert run_hook(command) == 0


def test_ignores_other_tools() -> None:
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)
    assert result.returncode == 0
