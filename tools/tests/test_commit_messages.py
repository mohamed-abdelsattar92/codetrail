"""Tests for the commit message rules: commitlint.config.mjs, which the commit-msg hook uses, and
commitlint.ci.config.mjs, which CI uses (AGENTS.md, Workflow). Adapted from hamesh-monorepo's."""
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

BODY = """What
- Adds the thing.

Why
- The design asks for it.

Alternatives considered
- None.

Risks
- None.

Agent and model
- Claude Code.

Co-Authored-By: Claude <noreply@anthropic.com>
"""


def lint(message: str, config: str = "commitlint.config.mjs") -> int:
    return subprocess.run(["pnpm", "exec", "commitlint", "--config", config], cwd=ROOT, input=message,
                          capture_output=True, text=True).returncode


def test_accepts_a_complete_message() -> None:
    assert lint("build(tools): add the hooks\n\n" + BODY) == 0


@pytest.mark.parametrize("scope", ["config", "repo", "extract", "facts", "claude", "generate", "guide", "web",
                                   "bridge", "learn", "tools", "docs", "adr", "ci", "deps"])
def test_accepts_codetrails_scopes(scope: str) -> None:
    assert lint(f"docs({scope}): update the notes\n\n" + BODY) == 0


def test_accepts_merge_commits() -> None:
    assert lint("Merge branch 'feature/x' into develop\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n") == 0


@pytest.mark.parametrize("message", [
    "build(tools): add the hooks\n\n" + BODY.replace("Why\n- The design asks for it.\n", ""),
    "build(tools): add the hooks\n\n" + BODY.replace("- The design asks for it.\n", ""),
    "build(tools): add the hooks\n",
    "build: add the hooks\n\n" + BODY,
    "build(website): add the hooks\n\n" + BODY,
    "docs(api): add the hooks\n\n" + BODY,
    "docs(ios): add the hooks\n\n" + BODY,
    "update the hooks\n\n" + BODY,
    "Build(tools): Add the hooks\n\n" + BODY,
])
def test_rejects_bad_messages(message: str) -> None:
    assert lint(message) != 0


DEPENDABOT = """build(deps-dev): bump ruff from 0.16.10 to 0.16.11

Bumps [ruff](https://github.com/astral-sh/ruff) from 0.16.10 to 0.16.11.

---
updated-dependencies:
- dependency-name: ruff
  dependency-version: 0.16.11
...

Signed-off-by: dependabot[bot] <support@github.com>
"""


def test_ci_accepts_dependabot_security_updates() -> None:
    assert lint(DEPENDABOT, "commitlint.ci.config.mjs") == 0


def test_the_commit_hook_never_skips_the_rules_for_dependabots_line() -> None:
    assert lint(DEPENDABOT) != 0
    assert lint("anything\n\nSigned-off-by: dependabot[bot] <support@github.com>\n") != 0


@pytest.mark.parametrize("message", [
    "build(tools): add the hooks\n\nWhat\n- mentions Signed-off-by: dependabot[bot] <support@github.com> inline\n",
    "anything\n\nSigned-off-by: dependabot[bot] <support@github.com>\nMore text after the sign-off\n",
])
def test_ci_skips_only_a_final_dependabot_sign_off(message: str) -> None:
    assert lint(message, "commitlint.ci.config.mjs") != 0


def test_ci_keeps_every_other_rule() -> None:
    assert lint("build(tools): add the hooks\n\n" + BODY, "commitlint.ci.config.mjs") == 0
    assert lint("update the hooks\n\n" + BODY, "commitlint.ci.config.mjs") != 0


INLINE = ("What: Adds the thing.\n\nWhy: The design asks for it.\n\nAlternatives considered: None.\n\nRisks: None.\n\n"
          "Agent and model: Claude Code\n")


def test_accepts_sections_written_inline() -> None:
    assert lint("build(tools): add the hooks\n\n" + INLINE) == 0


@pytest.mark.parametrize("why", ["Why:\n\n", "Why:   \n\n"])
def test_rejects_an_empty_inline_why(why: str) -> None:
    assert lint("build(tools): add the hooks\n\n" + INLINE.replace("Why: The design asks for it.\n\n", why)) != 0
