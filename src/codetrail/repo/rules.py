"""The exclusion rules (design section 3.3; ADR 0001).

Built-in secret patterns come first and nothing can override them. Ignore lines, in gitignore syntax, come from the
target's `.codetrailignore` and the founder's ignore file, in that order.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from enum import StrEnum

from pathspec import GitIgnoreSpec

# Environment files, Terraform variables, private keys and credential files: never read, whatever the rules say.
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
# gitleaks obeys these from the folder it scans, so they never reach source/.
SCANNER_SETTINGS_PATTERNS: tuple[str, ...] = (".gitleaksignore", ".gitleaks.toml")
MAX_NAME_BYTES = 255  # the longest file or folder name APFS, ext4 and NTFS store
MAX_PATH_BYTES = 4096  # PATH_MAX on Linux; macOS's is 1024, so a longer path fails there when it is written


# Code points HFS+ ignores in names, so ".g\u200cit" lands as ".git" (git's is_hfs_dotgit checks the same ones).
IGNORED_IN_NAMES = dict.fromkeys([*range(0x200C, 0x2010), *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF])


def on_disk_key(path: str) -> str:
    """The name a case-insensitive, normalizing file system (APFS, HFS+) sees for a path."""
    return unicodedata.normalize("NFC", path.translate(IGNORED_IN_NAMES)).casefold()


class Reason(StrEnum):
    SECRET_PATTERN = "secret pattern"  # noqa: S105 - a reason label, not a password
    IGNORED = "ignore rules"
    GITLEAKS = "gitleaks"
    SCANNER_SETTINGS = "scanner settings"
    NOT_A_FILE = "symlink or submodule"
    UNSAFE_PATH = "unsafe path"
    TOO_LARGE = "too large"
    UNSCANNABLE = "unscannable"  # compressed text that can't be inflated whole for the scan


class ExclusionRules:
    """Decides whether a path in a target exists for Codetrail."""

    def __init__(self, ignore_lines: Sequence[str]) -> None:
        self._secret_files = GitIgnoreSpec.from_lines([*BUILTIN_FILE_PATTERNS, EXAMPLE_EXCEPTION])
        self._secret_directories = GitIgnoreSpec.from_lines(BUILTIN_DIRECTORY_PATTERNS)
        self._scanner_settings = GitIgnoreSpec.from_lines(SCANNER_SETTINGS_PATTERNS)
        # Ignore lines match in any case too, as git's do on macOS.
        self._ignored = GitIgnoreSpec.from_lines([line.lower() for line in ignore_lines])

    def reason(self, path: str) -> Reason | None:
        # Longer than a disk allows, so it can't be written anyway; the ignore-line limit (repo/refresh.py) holds for
        # names up to that length only, and longer ones would only make the patterns slower.
        encoded = path.encode("utf-8", "surrogateescape")
        if len(encoded) > MAX_PATH_BYTES or any(len(part) > MAX_NAME_BYTES for part in encoded.split(b"/")):
            return Reason.UNSAFE_PATH
        # The built-in patterns are lower-case and match in any case: macOS volumes ignore case.
        folded = path.lower()
        if self._secret_directories.match_file(folded) or self._secret_files.match_file(folded):
            return Reason.SECRET_PATTERN
        if self._scanner_settings.match_file(on_disk_key(path)):
            return Reason.SCANNER_SETTINGS
        if self._ignored.match_file(folded):
            return Reason.IGNORED
        return None
