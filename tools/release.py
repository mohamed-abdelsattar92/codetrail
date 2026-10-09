"""Releases Codetrail from develop: CONTRIBUTING.md, Releasing, steps 2 to 6. `just release <version>` runs it.

The founder runs it from their own terminal once step 1 (the version, the changelog section and the release notes) is
on origin/develop. It refuses coding-agent sessions first (AGENTS.md, Never do, rules 1 and 4), and checks everything
it can before it changes anything. Then it starts release/<version>, pushes it, opens the pull request into main,
waits for CI, merges the pull request, tags main, merges main back into develop, deletes the release branch, and
creates the GitHub release as a draft, which the founder checks and publishes.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import time
import tomllib

GUARD = pathlib.Path(__file__).resolve().parent / "git-hooks" / "agent-push-guard"
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# GitHub registers a new pull request's checks a few seconds after it opens: look this often, this many times
CHECKS_POLL_SECONDS = 5
CHECKS_POLL_TRIES = 24


class Refused(Exception):
    """A release can't start; nothing has been changed."""


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def check(version: str) -> None:
    """Raises Refused unless the release can start. Reads only, apart from fetching origin."""
    if not VERSION_PATTERN.fullmatch(version):
        raise Refused(f"give the version as X.Y.Z, such as 1.2.0 (got {version!r}).")
    if shutil.which("gh") is None:
        raise Refused("the GitHub CLI, gh, isn't installed; install it and sign in with `gh auth login`.")
    if git("branch", "--show-current") != "develop":
        raise Refused("run it on develop.")
    if git("status", "--porcelain"):
        raise Refused("the working tree has changes; commit or stash them first.")
    project_version = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"]
    if project_version != version:
        raise Refused(f"pyproject.toml says {project_version}; set the version first (Releasing, step 1).")
    if f'__version__ = "{version}"' not in pathlib.Path("src/codetrail/__init__.py").read_text():
        raise Refused(f"src/codetrail/__init__.py doesn't say {version}; set the version first (Releasing, step 1).")
    if not re.search(rf"^## \[{re.escape(version)}\] - ", pathlib.Path("CHANGELOG.md").read_text(), re.MULTILINE):
        raise Refused(f"CHANGELOG.md has no {version} section (Releasing, step 1).")
    notes = pathlib.Path(f"docs/releases/v{version}.md")
    if not notes.is_file():
        raise Refused(f"{notes} is missing (Releasing, step 1).")
    tag = f"v{version}"
    if git("tag", "--list", tag):
        raise Refused(f"the tag {tag} already exists.")
    try:
        git("fetch", "--quiet", "origin")
    except subprocess.CalledProcessError as error:
        raise Refused(f"couldn't fetch origin: {error.stderr.strip()}") from error
    if git("ls-remote", "--tags", "origin", f"refs/tags/{tag}"):
        raise Refused(f"the tag {tag} already exists on origin.")
    if git("rev-parse", "develop") != git("rev-parse", "origin/develop"):
        raise Refused("develop isn't the same as origin/develop; push or pull first, and let CI pass.")


def step(*command: str) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)


def wait_for_checks(branch: str) -> None:
    """`gh pr checks --watch` fails until the pull request's checks are registered, so wait for them first."""
    for _ in range(CHECKS_POLL_TRIES):
        result = subprocess.run(["gh", "pr", "checks", branch], capture_output=True, text=True, check=False)
        if "no checks reported" not in result.stdout + result.stderr:
            return
        time.sleep(CHECKS_POLL_SECONDS)


def release(version: str) -> None:
    branch, tag, notes = f"release/{version}", f"v{version}", f"docs/releases/v{version}.md"
    step("git", "flow", "release", "start", version)
    step("git", "push", "-u", "origin", branch)
    step("gh", "pr", "create", "--base", "main", "--head", branch, "--title", f"Release {version}",
         "--body-file", notes)
    wait_for_checks(branch)
    step("gh", "pr", "checks", branch, "--watch", "--fail-fast")
    step("gh", "pr", "merge", branch, "--merge")
    step("git", "checkout", "main")
    step("git", "pull", "--ff-only", "origin", "main")
    step("git", "tag", "-a", tag, "-m", f"Codetrail {version}")
    step("git", "push", "origin", tag)
    step("git", "checkout", "develop")
    step("git", "merge", "--no-ff", "--no-edit", "main")
    step("git", "push", "origin", "develop")
    step("git", "branch", "-d", branch)
    step("git", "push", "origin", "--delete", branch)
    step("gh", "release", "create", tag, "--draft", "--verify-tag", "--title", f"Codetrail {version}",
         "--notes-file", notes)
    print(f"\nThe draft release {tag} is ready. Check it on GitHub, then publish it:\n"
          f"  gh release edit {tag} --draft=false\n"
          "Once it's published, the release and its tag can't be changed.")


def main(argv: list[str]) -> int:
    if subprocess.run(["sh", str(GUARD), "Release"], check=False).returncode != 0:
        return 1
    version = argv[1] if len(argv) == 2 else ""
    try:
        check(version)
    except Refused as reason:
        print(f"Release refused: {reason} Nothing was changed.", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"Release refused: `{' '.join(error.cmd)}` failed: {error.stderr.strip()} Nothing was changed.",
              file=sys.stderr)
        return 1
    try:
        release(version)
    except subprocess.CalledProcessError as error:
        print(f"\nRelease stopped: `{' '.join(error.cmd)}` failed. The steps above it are done; finish from that "
              "step in CONTRIBUTING.md, Releasing.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
