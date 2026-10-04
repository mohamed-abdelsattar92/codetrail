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
    # security review of Phase 0, finding 1: aliases, config and nesting that hid a push
    "git -c alias.p=push p --no-verify origin develop",
    "git -c Alias.p=push p origin develop",
    "git -c credential.helper=store fetch",
    "git -c url.git@x:.insteadOf=https://x/ fetch",
    "git -c remote.origin.pushurl=x fetch",
    "git --config-env=alias.p=PUSH p",
    "git config Alias.p push",
    "git config Credential.helper store",
    "git config Url.git@x:.insteadOf https://x/",
    "git config --global alias.p push",
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p",
    "GIT_CONFIG_PARAMETERS=\"'alias.p=push'\" git p",
    "GIT_DIR=/tmp/x git log",
    "export GIT_CONFIG_COUNT=1",
    "git p --no-verify origin develop",
    "git anything --no-verify",
    "eval eval eval eval eval git push --no-verify origin develop",
    "bash -c \"bash -c \\\"bash -c 'bash -c \\\\\\\"bash -c git-status\\\\\\\"'\\\"\"",
    # finding 2: moving the review's trust anchor by hand
    "git symbolic-ref refs/remotes/origin/develop refs/heads/feature/x",
    "echo abc > .git/refs/remotes/origin/develop",
    "printf x >> .git/packed-refs",
    "cp /tmp/x .git/refs/remotes/origin/develop",
    "tee .git/config < /tmp/x",
    "mv /tmp/hook .git/hooks/pre-push",
    "rm .git/hooks/pre-push",
    "ln -sf /tmp/x .git/hooks/pre-push",
    "sed -i '' s/a/b/ .git/config",
    # finding 3: rewriting history, deleting branches and tags, releasing (rules 3 and 4)
    "git reset --hard HEAD~1",
    "git reset --hard origin/develop",
    "git rebase develop",
    "git rebase -i HEAD~3",
    "git filter-branch --tree-filter x HEAD",
    "git filter-repo --path x",
    "git branch -D feature/x",
    "git branch -d feature/x",
    "git branch --delete feature/x",
    "git branch -f develop HEAD~1",
    "git branch -M main",
    "git tag v1.0",
    "git tag -a v1.0 -m x",
    "git tag -d v1.0",
    "git tag -f v1.0",
    "git checkout main",
    "git switch main",
    "git merge --no-ff develop main",
    "git push origin :feature/x",
    # second review of Phase 0: more ways to write config, run a program, or move refs
    "cd .git && printf x >> config",
    "cd repo/.git",
    "printf '[alias]'>>.git/config",
    "printf x >> \"$(git rev-parse --git-dir)/config\"",
    "git -c core.editor='git push --no-verify origin develop; true' commit --allow-empty",
    "git -c core.fsmonitor=x status",
    "git -c user.name=x commit -m 'docs: x'",
    "GIT_EDITOR='git push' git commit",
    "GIT_SEQUENCE_EDITOR=x git rebase -i HEAD~1",
    "EDITOR=x git commit",
    "VISUAL=x git commit",
    "GIT_EXTERNAL_DIFF=x git diff",
    "GIT_PAGER=x git log",
    "git config --replace-all alias.p 'push --no-verify' --get",
    "git fetch /tmp/repo +feature/x:remotes/origin/develop",
    "git fetch ./ feature/x:remotes/origin/develop",
    "git fetch file:///tmp/repo develop",
    "git fetch origin develop:develop",
    "git branch -dr origin/develop",
    "git branch -df x",
    "git branch --del x",
    "git branch --forc main HEAD",
    "git reset --har HEAD~1",
    "git worktree add ../w main",
    "git tag -i v1.0.0",
    "git tag --sort=refname v1.0.0",
    "mise exec -- git push origin develop",
    "mise exec git@2 -- git push",
    "pnpm exec git push",
    "mise exec -- git commit --no-verify -m x",
    # third review of Phase 0: persistent config, global config, globs, pull, rewinding develop, mise spellings
    "git config core.fsmonitor 'git push --no-verify origin develop'",
    "git config core.editor 'x; true'",
    "git config sequence.editor x",
    "git config diff.external x",
    "git config gpg.program x",
    "git config core.askpass x",
    "git config core.pager x",
    "git config --local core.fsmonitor x",
    "git config set core.fsmonitor x",
    "printf x >> ~/.gitconfig",
    "tee -a ~/.config/git/config < /tmp/x",
    "printf x >> .gi?/config",
    "cp x .gi?/hooks/pre-push",
    "rm .gi*/refs/remotes/origin/develop",
    "pushd .git && printf x >> config",
    "git pull /tmp/repo +feature/x:remotes/origin/develop",
    "git pull origin develop:develop",
    "git checkout -B develop HEAD~3",
    "git switch -C develop abc123",
    "git switch -C main",
    "git checkout -",
    "git switch -",
    "git checkout @{-1}",
    "mise exec -c 'git push --no-verify origin develop'",
    "mise x -- git push origin develop",
    "mise --verbose exec -- git push",
    "mise exec",
    "pnpm --dir . exec git push --no-verify",
    # final review of Phase 0: expansions hiding the subcommand, config sections, git's own file writers, pnpm -c
    "C=push F=--no-verify; git $C $F origin develop",
    "git push $F",
    "git \"$X\" origin develop",
    "git `echo push` origin develop",
    "U=update-ref; git $U refs/remotes/origin/develop abc",
    "git config gitflow.x.p 'push --no-verify'",
    "git config --rename-section gitflow.x alias",
    "git config rename-section gitflow.x alias",
    "git config --remove-section user",
    "git log -1 --format=x --output=.git/config",
    "git --work-tree=.git checkout HEAD -- config",
    "git --git-dir=/tmp/x log",
    "git checkout-index -f --prefix=.git/ config",
    "pnpm exec -c 'git push --no-verify origin develop'",
    "pnpm -c exec 'git push --no-verify origin develop'",
    "pnpm --shell-mode exec 'git push'",
]

ALLOWED = [
    "git status",
    "git log --oneline -5",
    "git commit -m 'docs: x'",
    "git fetch --prune -q origin",
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
    "mise exec -- just ci",
    "mise exec -- uv run pytest -q",
    "mise exec -- git flow feature finish x --no-ff --no-push --keepremote --no-fetch",
    "pnpm exec commitlint --edit .git/COMMIT_EDITMSG",
    "git config user.name 'Test Author'",
    "git config user.email author@example.com",
    "git config gitflow.branch.feature.prefix feature/",
    "git config gitflow.branch.feature.startpoint develop",
    "git commit -m \"$(cat <<'EOF'\ndocs: x\n\nWhy: y\nEOF\n)\"",
    "pnpm exec -c 'just ci'",
    "git pull origin develop",
    "git switch -c feature/y",
    "mise x -- just ci",
    "mise exec -c 'just ci'",
    "git fetch --prune origin develop",
    "grep -c '<<' notes.txt",
    "git tag",
    "git tag -l 'v*'",
    "git tag --list",
    "git tag --contains HEAD",
    "git branch",
    "git branch --show-current",
    "git branch -a",
    "git reset HEAD -- file.txt",
    "git config --get core.hooksPath",
    "git config --get-regexp gitflow",
    "git worktree list",
    "git worktree add ../w feature/x",
    "git fetch origin",
    "git config user.name",
    "git config --get-regexp gitflow",
    "git symbolic-ref --short HEAD",
    "git switch -c feature/x develop",
    "git switch develop",
    "git checkout -b feature/x develop",
    "cat .git/HEAD",
    "ls .git/hooks",
    "git log --format=%H -n 1 > /tmp/out.txt",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_blocks_push_forms(command: str) -> None:
    assert run_hook(command) == 2


@pytest.mark.parametrize("command", ALLOWED)
def test_allows_everyday_commands(command: str) -> None:
    assert run_hook(command) == 0


def test_fails_closed_on_unreadable_input() -> None:
    result = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True)
    assert result.returncode == 2


def test_ignores_other_tools() -> None:
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True)
    assert result.returncode == 0


@pytest.mark.parametrize("command", ["git commit --amend -m 'docs: x'", "git reset --soft HEAD~1", "git reset HEAD~1"])
@pytest.mark.parametrize("branch", ["develop", "main"])
def test_blocks_rewinding_protected_branches(tmp_path: pathlib.Path, branch: str, command: str) -> None:
    subprocess.run(["git", "init", "-q", "-b", branch, str(tmp_path)], check=True)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env, cwd=tmp_path)
    assert result.returncode == 2


@pytest.mark.parametrize("command", ["git commit --amend -m 'docs: x'", "git reset --soft HEAD~1"])
def test_allows_rewinding_a_feature_branch(tmp_path: pathlib.Path, command: str) -> None:
    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(tmp_path)], check=True)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {**__import__("os").environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    result = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, env=env, cwd=tmp_path)
    assert result.returncode == 0
