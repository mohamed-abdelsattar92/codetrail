# Phase 0: Engineering setup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `just ci` passes on an empty `codetrail` package, and the "Never do" rules are enforced by tools: git hooks, Claude Code permission rules and hooks, Codex rules, and CI.

**Architecture:** Hamesh's engineering setup (decision 11), copied and adapted rather than rewritten: mise pins the tools, `just` runs everything, lefthook runs small hook scripts in `tools/git-hooks/`, commitlint checks messages, `.claude/` and `.codex/` hold the agent guardrails. The Python package is a uv project with a `src/` layout.

**Tech Stack:** mise, just, uv, Python 3.14, lefthook, gitleaks, git-flow-next, Node + pnpm (commitlint only), ruff, mypy, pytest, GitHub Actions.

**Spec:** `docs/design/2026-10-05-codetrail-design.md` (sections 2.1, 11, 13) and `docs/design/brainstorm-decisions.md` (decisions 11 and 12).

## Global Constraints
- Tool versions exactly as Hamesh pins them: python 3.14.7, uv 0.12.18, just 1.58.0, lefthook 2.1.14, gitleaks 8.30.1, node 24.21.0, pnpm 12.6.0, git-flow-next 2.0.0.
- commitlint 21.2.3 and @commitlint/config-conventional 21.2.3.
- Commit types: feat, fix, refactor, perf, test, docs, build, ci, chore, revert. Scopes: config, repo, extract, facts, claude, generate, guide, web, bridge, learn, tools, docs, adr, ci, deps.
- Commit bodies have the sections What, Why, Alternatives considered, Risks, Agent and model; "Why" is required and non-empty.
- Every GitHub action is pinned to a full commit SHA; `actions/checkout` uses `persist-credentials: false`; workflow `permissions: contents: read`.
- Never write to a target repository; Hamesh is read with `cat`/`git show` only, to copy its scripts.

## Review Focus
1. A push attempted from this Claude Code session (any form) must be refused by the Claude hook and, as a backstop, by the pre-push hook — covered by the copied `test_block_push.py` and `test_pre_push.py`.
2. A commit without a "Why" section, or with an unknown scope such as `api`, must be refused — `test_commit_messages.py` includes Codetrail's scopes and refuses Hamesh-only ones.
3. A Bash command naming `.env` or a `.tfvars` file must be refused by `block_secret_reads.py` — the pattern gains `.tfvars` and a test for it.
4. The vendored Mermaid file planned for Phase 3 (~3 MB) must not trip the 1 MB file cap — `check_files.py` treats `src/codetrail/web/static/vendor/` as an asset folder, with a test.
5. The reviewer's audit command must match Codetrail's lockfiles (root `pnpm-lock.yaml`, `pyproject.toml`, `uv.lock`), or every review would list it as "Not checked" — `test_reviewer_allowlist.py` keeps checklist and hook equal.

---

### Task 1: Tool pins, the uv project and the justfile

**Files:**
- Create: `.mise.toml`, `.gitignore`, `pyproject.toml`, `src/codetrail/__init__.py`, `src/codetrail/__main__.py`, `src/codetrail/cli.py`, `tests/unit/test_cli.py`, `justfile`
- Generated: `uv.lock`

**Interfaces:**
- Produces: `codetrail.cli.main(argv: list[str] | None = None) -> int`; console script `codetrail`; `codetrail.__version__ = "0.1.0"`.

- [ ] **Step 1: Write `.mise.toml`, `.gitignore` and `pyproject.toml`**

`.mise.toml`:
```toml
# Pinned tools for codetrail (decision 11). Once per clone: `mise trust && mise install`, then `just setup`.
[tools]
python = "3.14.7"
uv = "0.12.18"
just = "1.58.0"
lefthook = "2.1.14"
gitleaks = "8.30.1"
node = "24.21.0"
pnpm = "12.6.0"
"github:gittower/git-flow-next" = "2.0.0"
```

`.gitignore`: macOS, secrets (`.env`, `.env.*`, `!.env.example`, `*.pem`, `*.p8`, `*.p12`, `*.key`), Python caches (`__pycache__/`, `*.py[cod]`, `.venv/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`), `node_modules/`, `*.mo`, `.claude/settings.local.json`, `mise.local.toml`, `.mise.local.toml`.

`pyproject.toml`:
```toml
[project]
name = "codetrail"
version = "0.1.0"
description = "Turn a git repository into a local learning guide that keeps up with it."
readme = "README.md"
requires-python = ">=3.14"
dependencies = []

[project.scripts]
codetrail = "codetrail.cli:main"

[dependency-groups]
dev = ["pytest==9.1.1", "ruff", "mypy"]

[build-system]
requires = ["uv_build>=0.12,<0.13"]
build-backend = "uv_build"

[tool.ruff]
line-length = 120
target-version = "py314"
src = ["src", "tests"]
extend-exclude = [".claude", "tools"]  # Hamesh's hook scripts and their tests, copied with their own style

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "S", "RUF"]
ignore = ["S101"]  # assert is how pytest tests

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S603", "S607"]

[tool.mypy]
strict = true
files = ["src", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests", "tools/tests"]
markers = ["slow: end-to-end tests the pre-push hook skips", "live: tests that call the real Claude"]
addopts = "-m 'not live'"
```
(Pin ruff and mypy to the versions `uv add --dev` resolves.)

- [ ] **Step 2: Write the failing test** `tests/unit/test_cli.py`:
```python
"""The command line exists and reports its version."""

import pytest

from codetrail import __version__
from codetrail.cli import main


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err
```

- [ ] **Step 3: Run it to see it fail**: `mise exec -- uv run pytest tests/unit/test_cli.py -q` → ImportError.

- [ ] **Step 4: Implement** `src/codetrail/__init__.py` (`__version__ = "0.1.0"`), `__main__.py` (`raise SystemExit(main())`) and `cli.py`:
```python
"""Codetrail's command line."""

import argparse

from codetrail import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codetrail", description="Turn a git repository into a local learning guide.")
    parser.add_argument("--version", action="version", version=f"codetrail {__version__}")
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help(file=__import__("sys").stderr)
        return 2
    return 0
```
(Import `sys` normally at the top; the snippet is shortened only in this line.)

- [ ] **Step 5: Run the tests** → PASS.

- [ ] **Step 6: Write the `justfile`** with recipes: `default`, `setup` (mise install, `uv sync`, `pnpm install --frozen-lockfile`, `just _install-hooks`, `git flow config sync`), `ci: check-repo lint typecheck test`, `check-repo` (gitleaks over history; `check_files.py` over tracked files), `check-commits from to config`, `lint: lint-just lint-python`, `lint-just` (`just --fmt --check --unstable`), `lint-python` (`uv run ruff check` and `uv run ruff format --check`), `format`, `typecheck` (`uv run mypy`), `test` (`uv run pytest -q`), `test-quick` (`uv run pytest -q -m "not slow and not live" tests/unit tests/api tools/tests`), `test-live` (`uv run pytest -q -m live --override-ini addopts=`), `_install-hooks` (as Hamesh's).

- [ ] **Step 7: Run** `mise exec -- just lint typecheck test` → all pass. **Commit** `build(tools): pin the tools and start the uv project`.

### Task 2: Commit message rules

**Files:**
- Create: `package.json`, `pnpm-lock.yaml` (generated), `commitlint.config.mjs`, `commitlint.ci.config.mjs`, `tools/tests/conftest.py`, `tools/tests/test_commit_messages.py`

- [ ] **Step 1: Copy** Hamesh's `tools/tests/test_commit_messages.py` and `conftest.py`; adapt: BODY drops "Requirement IDs"; accepted scopes `["adr", "extract", "bridge", "web", "tools"]`; add rejected cases `docs(api): …` and `docs(ios): …`.
- [ ] **Step 2: Run** → FAIL (no commitlint).
- [ ] **Step 3: Write** `package.json` (private, `packageManager: pnpm@12.6.0`, devDependencies commitlint 21.2.3 and config-conventional 21.2.3), copy both commitlint configs with Codetrail's TYPES, SCOPES and SECTIONS (no "Requirement IDs"); run `mise exec -- pnpm install`.
- [ ] **Step 4: Run** `mise exec -- uv run pytest -q tools/tests/test_commit_messages.py` → PASS. **Commit** `build(tools): check commit messages with commitlint`.

### Task 3: Git hooks and the file check

**Files:**
- Create: `tools/git-hooks/{agent-push-guard,run-lefthook,pre-commit,commit-msg,pre-push}`, `tools/hooks/check_files.py`, `lefthook.yml`, `tools/tests/test_pre_push.py`, `tools/tests/test_check_files.py`

- [ ] **Step 1: Copy the tests** `test_pre_push.py` (rename `HAMESH_AGENT` to `CODETRAIL_AGENT`) and `test_check_files.py`, rewritten for Codetrail's rules: files over 1 MB refused except under `src/codetrail/web/static/vendor/` (test: `src/codetrail/web/static/vendor/mermaid.min.js` at 3 MB allowed; `src/codetrail/cli.py` at 2 MB refused); no audio rules.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Copy the hook scripts** with "hamesh-monorepo" replaced by "codetrail" and `HAMESH_AGENT` by `CODETRAIL_AGENT`; write `check_files.py` with only the size rule and `ASSET_PREFIXES = ("src/codetrail/web/static/vendor/",)`; write `lefthook.yml`:
```yaml
no_auto_install: true
min_version: 2.1.14
pre-commit:
  parallel: true
  jobs:
    - name: secrets
      run: gitleaks git --pre-commit --staged --redact --no-banner
    - name: file size
      run: python3 tools/hooks/check_files.py {staged_files}
    - name: justfile format
      glob: "justfile"
      run: just --fmt --check --unstable
    - name: python lint
      glob: "*.py"
      run: just lint-python
commit-msg:
  jobs:
    - name: commitlint
      run: pnpm exec commitlint --edit {1}
pre-push:
  jobs:
    - name: quick tests
      run: just test-quick
```
- [ ] **Step 4: Run** the tests → PASS; run `mise exec -- just _install-hooks`. **Commit** `build(tools): run the checks in git hooks with lefthook` (this commit is the first checked by the hooks).

### Task 4: Claude Code guardrails and the security checklist

**Files:**
- Create: `.claude/settings.json`, `.claude/hooks/{block_push.py,block_secret_reads.py,reviewer_allowlist.py}`, `.claude/agents/security-reviewer.md`, `docs/security/review-checklist.md`, `tools/tests/{test_block_push.py,test_block_secret_reads.py,test_reviewer_allowlist.py}`

- [ ] **Step 1: Copy the three test files**; replace Hamesh paths in examples where they name Hamesh-only files; add to the secret guard's BLOCKED `"cat infra/envs/dev/terraform.tfvars"` and `"cat ~/.pypirc"`; point the allowlist tests at Codetrail's audit command (Step 3).
- [ ] **Step 2: Run** → FAIL (no hooks).
- [ ] **Step 3: Copy the hooks**, replacing "hamesh-monorepo" with "codetrail"; `SECRET_NAME` gains `|.+\.tfvars(\.json)?`; `SECRET_PATH` gains `|(^|/)\.pypirc$`. In `reviewer_allowlist.py`, the AUDIT becomes:
```
export MISE_EXEC_AUTO_INSTALL=false; js=$(mktemp -d) py=$(mktemp -d) && git show <sha>:pnpm-lock.yaml > "$js/pnpm-lock.yaml" && git show <sha>:pyproject.toml > "$py/pyproject.toml" && git show <sha>:uv.lock > "$py/uv.lock" && mise exec -- pnpm --dir "$js" audit --registry=https://registry.npmjs.org/; mise exec -- uv audit --frozen --no-build --no-config --directory "$py"; rm -rf "$js" "$py"
```
and SITES drops the Hamesh vendors (cloudflare, astro, posthog, neon, apple, android, google cloud, terraform registry, sqlalchemy) and gains `jinja.palletsprojects.com`, `markdown-it-py.readthedocs.io`, `mermaid.js.org`, `tree-sitter.github.io`. `settings.json` is Hamesh's, plus `Read(./**/*.tfvars)`, `Read(./**/*.key)`, `Read(~/.pypirc)`. The agent file is Hamesh's with "Hamesh" → "Codetrail". The checklist is Hamesh's process, standards and report format, with the product sections replaced by Codetrail's: secrets and exclusions (design section 3), target repositories stay read-only, the local server and bridge (section 7.4), Claude as an agent (tool guard, no network, prompt injection; LLM and Agentic Top 10), errors and logging, agent guardrails, supply chain and CI.
- [ ] **Step 4: Run** → PASS. **Commit** `build(tools): add the Claude Code guardrails and the security reviewer`.

### Task 5: Codex rules and git-flow

**Files:** Create `.codex/rules/no-push.rules` (Hamesh's, renamed), `.gitflow` (Hamesh's, renamed).
- [ ] **Step 1:** Copy and adapt both; run `mise exec -- git flow config sync`; check `git config --get-regexp gitflow | head` lists the branches.
- [ ] **Step 2: Commit** `build(tools): add the Codex rules and the git-flow settings`.

### Task 6: CI and Dependabot

**Files:** Create `.github/workflows/ci.yml`, `.github/dependabot.yml`.
- [ ] **Step 1:** One job `ci` on `ubuntu-24.04`: checkout (SHA `3d3c42e5aac5ba805825da76410c181273ba90b1`, `fetch-depth: 0`, `persist-credentials: false`), mise-action (SHA `c2a87611a18de5b3828c5652fe268e992400cb5c`, `install_args: gitleaks just python uv node pnpm github:gittower/git-flow-next`), `pnpm install --frozen-lockfile`, `uv sync --locked`, `just ci`, and the commit-message check on develop pushes as Hamesh does. Dependabot: npm `/`, uv `/`, github-actions `/`, security updates only.
- [ ] **Step 2:** Validate the YAML parses (`python3 -c "import yaml"` is not available — use `uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`). **Commit** `ci(ci): run just ci on every push, and Dependabot security updates`.

### Task 7: Documents, review, finish

- [ ] **Step 1:** README status (Phase 0 done; setup commands), AGENTS.md (scope list, commands are live, Phase 0 landed), CLAUDE.md (guardrails now enforced). **Commit** `docs(docs): describe the engineering setup`.
- [ ] **Step 2:** Run `mise exec -- just ci`; all pass.
- [ ] **Step 3:** Security review with the `security-reviewer` agent; fix critical/high findings; finish the feature with the merge message carrying the verdict.
