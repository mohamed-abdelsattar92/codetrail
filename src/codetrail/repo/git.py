"""Runs git for Codetrail: argument lists only, never a shell, never a prompt."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from codetrail.errors import CodetrailError

# How long a git stopped early gets to remove its lock files before it's killed; it needs only a moment.
STOP_WAIT_SECONDS = 5


def run_git(
    arguments: Sequence[str],
    *,
    git_dir: Path | None = None,
    work_tree: Path | None = None,
    input: bytes | None = None,
    allowed_exit_codes: Sequence[int] = (0,),
) -> bytes:
    """Runs git and returns its standard output; any other exit code raises a CodetrailError with git's message."""
    command = ["git"]
    if git_dir is not None:
        command.append(f"--git-dir={git_dir}")
    if work_tree is not None:
        command.append(f"--work-tree={work_tree}")
    command.extend(arguments)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    # Run from the file system's root and stop repository discovery there: git never finds a repository (a
    # target's, or another user's in a shared temporary folder) from where it runs.
    environment["GIT_CEILING_DIRECTORIES"] = "/"
    with subprocess.Popen(  # noqa: S603
        command, stdin=subprocess.PIPE if input is not None else None, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, cwd="/",
    ) as process:  # fmt: skip
        try:
            stdout, stderr = process.communicate(input)
        except BaseException:
            # Stopped early (Ctrl-C, a hangup or terminate signal): SIGTERM lets git remove its lock files, which
            # SIGKILL would leave behind to fail every later command in that repository.
            try:
                process.terminate()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(STOP_WAIT_SECONDS)
            finally:
                process.kill()  # does nothing once git has exited
            raise
    if process.returncode not in allowed_exit_codes:
        message = stderr.decode("utf-8", "replace").strip() or f"exit code {process.returncode}"
        raise CodetrailError(f"git {arguments[0]} failed: {message}")
    return stdout
