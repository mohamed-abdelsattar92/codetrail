"""The exclusion rules (design section 3.3; ADR 0001).

Built-in secret patterns come first and nothing can override them. Ignore lines, in gitignore syntax, come from the
target's `.codetrailignore` and the founder's ignore file, in that order.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pathspec import GitIgnoreSpec

# Started from hamesh-monorepo's secret-read hook, with Terraform variables, private keys and credential files added.
BUILTIN_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    ".dev.vars",
    "*.p8",
    "*.p12",
    "*.pem",
    "*.keystore",
    "*.jks",
    "*.tfstate",
    "*.tfstate.backup",
    "*.tfvars",
    "*.tfvars.json",
    "*.key",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.ppk",
    ".netrc",
    ".npmrc",
    ".pypirc",
)
# Templates such as .env.example hold no secrets. The exception applies to file patterns only, so nothing under an
# excluded folder comes back.
EXAMPLE_EXCEPTION = "!*.example"
BUILTIN_DIRECTORY_PATTERNS: tuple[str, ...] = (".terraform/",)


class Reason(StrEnum):
    SECRET_PATTERN = "secret pattern"  # noqa: S105 - a reason label, not a password
    IGNORED = "ignore rules"
    GITLEAKS = "gitleaks"
    NOT_A_FILE = "symlink or submodule"
    UNSAFE_PATH = "unsafe path"


class ExclusionRules:
    """Decides whether a path in a target exists for Codetrail."""

    def __init__(self, ignore_lines: Sequence[str]) -> None:
        self._secret_files = GitIgnoreSpec.from_lines([*BUILTIN_FILE_PATTERNS, EXAMPLE_EXCEPTION])
        self._secret_directories = GitIgnoreSpec.from_lines(BUILTIN_DIRECTORY_PATTERNS)
        self._ignored = GitIgnoreSpec.from_lines(ignore_lines)

    def reason(self, path: str) -> Reason | None:
        if self._secret_directories.match_file(path) or self._secret_files.match_file(path):
            return Reason.SECRET_PATTERN
        if self._ignored.match_file(path):
            return Reason.IGNORED
        return None
