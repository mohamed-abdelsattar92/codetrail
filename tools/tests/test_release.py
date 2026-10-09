"""Tests for tools/release.py: every refusal comes before it changes anything or reaches GitHub."""
import os
import pathlib
import pty
import select
import shutil
import subprocess
import sys

import pytest

RELEASE = pathlib.Path(__file__).resolve().parents[1] / "release.py"
AGENT_MARKERS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CODETRAIL_AGENT")
AGENT_SESSIONS = [
    {"CLAUDECODE": "1"},
    {"CLAUDE_CODE_ENTRYPOINT": "cli"},
    {"CODEX_SANDBOX": "seatbelt"},
    {"CODETRAIL_AGENT": "ci"},
]
IDENTITY = {
    "GIT_AUTHOR_NAME": "Founder",
    "GIT_AUTHOR_EMAIL": "founder@example.com",
    "GIT_COMMITTER_NAME": "Founder",
    "GIT_COMMITTER_EMAIL": "founder@example.com",
}
VERSION = "1.2.0"


@pytest.fixture
def fake_gh(tmp_path: pathlib.Path) -> pathlib.Path:
    """A folder with a `gh` that only records that it was called."""
    folder = tmp_path / "bin"
    folder.mkdir()
    gh = folder / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path / "gh-calls"}"\n')
    gh.chmod(0o755)
    return folder


@pytest.fixture
def env(fake_gh: pathlib.Path) -> dict[str, str]:
    """The founder's environment: no agent markers, a git identity, and the fake gh first on PATH."""
    clean = {k: v for k, v in os.environ.items() if k not in AGENT_MARKERS and not k.startswith("CODEX_")}
    clean.update(IDENTITY, PATH=f"{fake_gh}{os.pathsep}{os.environ['PATH']}")
    return clean


def git(repo: pathlib.Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: pathlib.Path, env: dict[str, str]) -> pathlib.Path:
    """A clone on develop with Releasing step 1 done for VERSION, and no origin."""
    path = tmp_path / "codetrail"
    files = {
        "pyproject.toml": f'[project]\nname = "codetrail"\nversion = "{VERSION}"\n',
        "src/codetrail/__init__.py": f'__version__ = "{VERSION}"\n',
        "CHANGELOG.md": f"# Changelog\n\n## [Unreleased]\n\n## [{VERSION}] - 2026-10-09\n",
        f"docs/releases/v{VERSION}.md": f"# Codetrail {VERSION}\n",
    }
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(path)], check=True)
    git(path, env, "add", "-A")
    git(path, env, "commit", "-q", "-m", "chore: start")
    return path


def run_release(repo: pathlib.Path, env: dict[str, str], *args: str) -> tuple[int, str]:
    """Run the script with its output on a pseudo-terminal, as in the founder's shell."""
    leader, follower = pty.openpty()
    try:
        code = subprocess.run([sys.executable, str(RELEASE), *args], cwd=repo, env=env,
                              stdin=subprocess.DEVNULL, stdout=follower, stderr=follower, check=False).returncode
        chunks = []
        while select.select([leader], [], [], 0)[0]:
            chunk = os.read(leader, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return code, b"".join(chunks).decode(errors="replace")
    finally:
        os.close(leader)
        os.close(follower)


def assert_refused_untouched(repo: pathlib.Path, env: dict[str, str], code: int, output: str) -> None:
    assert code == 1
    assert "Release refused" in output
    assert not (repo.parent / "gh-calls").exists()
    assert git(repo, env, "branch", "--list", "release/*") == ""
    assert git(repo, env, "tag", "--list") == ""


@pytest.mark.parametrize("marker", AGENT_SESSIONS)
def test_refuses_agent_sessions(repo: pathlib.Path, env: dict[str, str], marker: dict[str, str]) -> None:
    code, output = run_release(repo, {**env, **marker}, VERSION)
    assert_refused_untouched(repo, env, code, output)


def test_refuses_sessions_without_a_terminal(repo: pathlib.Path, env: dict[str, str]) -> None:
    result = subprocess.run([sys.executable, str(RELEASE), VERSION], cwd=repo, env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    assert_refused_untouched(repo, env, result.returncode, result.stderr)
    assert "without a terminal" in result.stderr


@pytest.mark.parametrize("version", ["", "1.2", "v1.2.0", "1.2.0.1", "1.2.0\n1.3.0", "1.2.0; ls", "1.2.0-rc1"])
def test_refuses_a_version_that_isnt_x_y_z(repo: pathlib.Path, env: dict[str, str], version: str) -> None:
    code, output = run_release(repo, env, version)
    assert_refused_untouched(repo, env, code, output)
    assert "X.Y.Z" in output


def test_refuses_without_a_version(repo: pathlib.Path, env: dict[str, str]) -> None:
    code, output = run_release(repo, env)
    assert_refused_untouched(repo, env, code, output)
    assert "X.Y.Z" in output


def test_refuses_without_gh(repo: pathlib.Path, env: dict[str, str], tmp_path: pathlib.Path) -> None:
    folder = tmp_path / "no-gh"
    folder.mkdir()
    for program in ("git", "sh", "env", "grep"):
        (folder / program).symlink_to(shutil.which(program) or program)
    code, output = run_release(repo, {**env, "PATH": str(folder)}, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "gh" in output


def test_refuses_off_develop(repo: pathlib.Path, env: dict[str, str]) -> None:
    git(repo, env, "checkout", "-q", "-b", "feature/other")
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "develop" in output


def test_refuses_with_uncommitted_changes(repo: pathlib.Path, env: dict[str, str]) -> None:
    (repo / "notes.txt").write_text("draft\n")
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "working tree" in output


def test_refuses_when_pyproject_has_another_version(repo: pathlib.Path, env: dict[str, str]) -> None:
    code, output = run_release(repo, env, "1.3.0")
    assert_refused_untouched(repo, env, code, output)
    assert "pyproject.toml" in output


def test_refuses_when_the_package_has_another_version(repo: pathlib.Path, env: dict[str, str]) -> None:
    (repo / "src/codetrail/__init__.py").write_text('__version__ = "1.1.0"\n')
    git(repo, env, "commit", "-q", "-am", "chore: old version")
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "__init__.py" in output


def test_refuses_without_a_changelog_section(repo: pathlib.Path, env: dict[str, str]) -> None:
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    git(repo, env, "commit", "-q", "-am", "chore: no section")
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "CHANGELOG.md" in output


def test_refuses_without_release_notes(repo: pathlib.Path, env: dict[str, str]) -> None:
    git(repo, env, "rm", "-q", f"docs/releases/v{VERSION}.md")
    git(repo, env, "commit", "-q", "-m", "chore: no notes")
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert f"docs/releases/v{VERSION}.md" in output


def test_refuses_when_the_tag_exists(repo: pathlib.Path, env: dict[str, str]) -> None:
    git(repo, env, "tag", f"v{VERSION}")
    code, output = run_release(repo, env, VERSION)
    assert code == 1
    assert "Release refused" in output
    assert f"v{VERSION} already exists" in output
    assert not (repo.parent / "gh-calls").exists()


def test_refuses_without_an_origin(repo: pathlib.Path, env: dict[str, str]) -> None:
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "origin" in output


def test_refuses_when_develop_differs_from_origin(repo: pathlib.Path, env: dict[str, str]) -> None:
    origin = repo.parent / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    git(repo, env, "remote", "add", "origin", str(origin))
    (repo / "README.md").write_text("unpushed\n")
    git(repo, env, "add", "README.md")
    git(repo, env, "commit", "-q", "-m", "docs: unpushed")
    code, output = run_release(repo, env, VERSION)
    assert_refused_untouched(repo, env, code, output)
    assert "origin/develop" in output


def test_refuses_when_origin_has_the_tag(repo: pathlib.Path, env: dict[str, str]) -> None:
    origin = repo.parent / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    subprocess.run(["git", "--git-dir", str(origin), "tag", f"v{VERSION}", "develop"], check=True)
    git(repo, env, "remote", "add", "origin", str(origin))
    # develop also differs from origin's, so the script would stop even if it missed the tag
    (repo / "README.md").write_text("unpushed\n")
    git(repo, env, "add", "README.md")
    git(repo, env, "commit", "-q", "-m", "docs: unpushed")
    code, output = run_release(repo, env, VERSION)
    assert code == 1
    assert f"v{VERSION} already exists on origin" in output
    assert not (repo.parent / "gh-calls").exists()
    assert git(repo, env, "branch", "--list", "release/*") == ""
