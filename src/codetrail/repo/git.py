"""Runs git for Codetrail: argument lists only, never a shell, never a prompt."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from codetrail.errors import CodetrailError


def run_git(
    arguments: Sequence[str],
    *,
    git_dir: Path | None = None,
    input: bytes | None = None,
    allowed_exit_codes: Sequence[int] = (0,),
) -> bytes:
    """Runs git and returns its standard output; any other exit code raises a CodetrailError with git's message."""
    command = ["git"]
    if git_dir is not None:
        command.append(f"--git-dir={git_dir}")
    command.extend(arguments)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(command, input=input, capture_output=True, env=environment, check=False)  # noqa: S603
    if result.returncode not in allowed_exit_codes:
        message = result.stderr.decode("utf-8", "replace").strip() or f"exit code {result.returncode}"
        raise CodetrailError(f"git {arguments[0]} failed: {message}")
    return result.stdout
