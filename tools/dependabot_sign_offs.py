"""CI's check of Dependabot's sign-off (security audit, low G12).

commitlint.ci.config.mjs skips the message of a commit whose last line is Dependabot's sign-off, since Dependabot's
security updates can't carry our sections. Anyone can write that line, so before commitlint runs, this asks GitHub
about each such commit in the push and fails unless GitHub reports it verified and authored by dependabot[bot].
It only reads, through `gh api` with the workflow's own read-only token.

Usage: python3 tools/dependabot_sign_offs.py <owner/repository> <before> <after>
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable

# The same line commitlint.ci.config.mjs skips: Dependabot's sign-off as the message's last line.
DEPENDABOT_SIGN_OFF = re.compile(r"\nSigned-off-by: dependabot\[bot\] <support@github\.com>\s*\Z")
DEPENDABOT = "dependabot[bot]"


def commits_between(before: str, after: str, cwd: object = None) -> list[tuple[str, str]]:
    """The commits commitlint checks for the range, as (sha, message), newest first."""
    output = subprocess.run(["git", "log", "-z", "--format=%H%n%B", f"{before}..{after}"], cwd=cwd,
                            capture_output=True, text=True, check=True).stdout
    return [tuple(record.split("\n", 1)) for record in output.split("\0") if record.strip()]


def problems(repository: str, commits: list[tuple[str, str]], run: Callable = subprocess.run) -> list[str]:
    """One line for each commit with Dependabot's sign-off that GitHub doesn't vouch for."""
    found = []
    for sha, message in commits:
        if not DEPENDABOT_SIGN_OFF.search(message):
            continue
        result = run(["gh", "api", "--method", "GET", f"repos/{repository}/commits/{sha}"],
                     capture_output=True, text=True, check=False)
        if result.returncode != 0:
            found.append(f"{sha}: GitHub couldn't be asked about it ({result.stderr.strip()[:200]})")
            continue
        answer = json.loads(result.stdout)
        author = (answer.get("author") or {}).get("login")
        verified = ((answer.get("commit") or {}).get("verification") or {}).get("verified") is True
        if author != DEPENDABOT or not verified:
            found.append(f"{sha}: ends with Dependabot's sign-off, but GitHub reports author {author!r}, "
                         f"{'verified' if verified else 'not verified'}")
    return found


def main(arguments: list[str]) -> int:
    repository, before, after = arguments
    found = problems(repository, commits_between(before, after))
    for line in found:
        print(line, file=sys.stderr)
    if found:
        print("Only Dependabot's own verified commits may skip the commit message rules.", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
