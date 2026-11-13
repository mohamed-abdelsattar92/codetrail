"""Tests for tools/dependabot_sign_offs.py: CI skips a commit message for Dependabot's sign-off only when GitHub
reports the commit verified and authored by dependabot[bot] (security audit, low G12)."""
import importlib.util
import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("dependabot_sign_offs", ROOT / "tools" / "dependabot_sign_offs.py")
dependabot_sign_offs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependabot_sign_offs)

SIGN_OFF = "build(deps): bump x\n\nBumps x.\n\nSigned-off-by: dependabot[bot] <support@github.com>\n"
DEPENDABOT = {"author": {"login": "dependabot[bot]"}, "commit": {"verification": {"verified": True, "reason": "valid"}}}


def fake_github(answers: dict[str, dict]):
    """A stand-in for `gh api repos/<repository>/commits/<sha>` that records what it was asked."""
    asked = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess:
        asked.append(command)
        sha = command[-1].rsplit("/", 1)[1]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(answers[sha]), stderr="")

    return run, asked


def test_a_verified_dependabot_commit_may_be_skipped() -> None:
    run, asked = fake_github({"a1": DEPENDABOT})
    assert dependabot_sign_offs.problems("owner/repo", [("a1", SIGN_OFF)], run) == []
    assert asked == [["gh", "api", "--method", "GET", "repos/owner/repo/commits/a1"]]


@pytest.mark.parametrize("answer", [
    {"author": {"login": "someone"}, "commit": {"verification": {"verified": True}}},
    {"author": {"login": "dependabot[bot]"}, "commit": {"verification": {"verified": False, "reason": "unsigned"}}},
    {"author": None, "commit": {"verification": {"verified": True}}},  # an author with no GitHub account
    {"author": {"login": "dependabot[bot]"}, "commit": {}},
])
def test_any_other_commit_with_the_sign_off_fails(answer: dict) -> None:
    run, _ = fake_github({"b2": answer})
    found = dependabot_sign_offs.problems("owner/repo", [("b2", SIGN_OFF)], run)
    assert len(found) == 1 and "b2" in found[0]


def test_commits_without_the_final_sign_off_are_left_to_commitlint() -> None:
    run, asked = fake_github({})
    commits = [("c3", "build(tools): add x\n\nWhat: x\n"),
               ("d4", "x\n\nSigned-off-by: dependabot[bot] <support@github.com>\nMore text after the sign-off\n")]
    assert dependabot_sign_offs.problems("owner/repo", commits, run) == []
    assert asked == []


def test_a_failed_api_call_fails_the_check() -> None:
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP 404")

    assert dependabot_sign_offs.problems("owner/repo", [("e5", SIGN_OFF)], run)


def test_reads_the_range_from_git(tmp_path: pathlib.Path) -> None:
    def commit(message: str) -> str:
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty", "-m", message], check=True)
        return subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    first = commit("first")
    second = commit(SIGN_OFF)
    third = commit("third\n\nbody")
    commits = dependabot_sign_offs.commits_between(first, third, cwd=tmp_path)
    assert [sha for sha, _ in commits] == [third, second]
    assert commits[1][1].endswith("Signed-off-by: dependabot[bot] <support@github.com>\n")


def test_ci_skips_exactly_the_line_this_check_vouches_for() -> None:
    config = (ROOT / "commitlint.ci.config.mjs").read_text()
    javascript = config.split("const DEPENDABOT_SIGN_OFF = /", 1)[1].split("/;", 1)[0]
    assert javascript.removesuffix("$") == dependabot_sign_offs.DEPENDABOT_SIGN_OFF.pattern.removesuffix(r"\Z")
    assert "\\s" not in javascript  # [ \t\r\n], not \s, which also takes other Unicode spaces


def test_only_plain_whitespace_may_follow_the_sign_off() -> None:
    assert not dependabot_sign_offs.DEPENDABOT_SIGN_OFF.search(SIGN_OFF.rstrip("\n") + "\u2028")
