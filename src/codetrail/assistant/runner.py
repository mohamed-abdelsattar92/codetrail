"""Runs a provider's program: the prompt on stdin, its output as lines, bounded in time (design section 15.5).

Each program runs in its own process group, which is killed when the call ends for any reason (done, failed, timed
out, or the reader stopped listening), so nothing it started keeps running. Its error output goes to a temporary file,
so a talkative program can't fill a pipe and stall.
"""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path

import anyio
from anyio.abc import Process

from codetrail.assistant import AssistantError

ERROR_LINE_CHARS = 300


async def run_program(
    command: Sequence[str], stdin_text: str, cwd: Path, environment: Mapping[str, str], timeout_seconds: float
) -> AsyncGenerator[str]:
    """Yields the program's output lines; raises AssistantError on a failure exit or when the time limit passes."""
    deadline = anyio.current_time() + timeout_seconds
    with tempfile.TemporaryFile() as errors:
        process = await anyio.open_process(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
        )
        try:
            assert process.stdin is not None and process.stdout is not None
            with _limit(deadline):
                await process.stdin.send(stdin_text.encode("utf-8"))
                await process.stdin.aclose()
            pending = b""
            while True:
                with _limit(deadline):
                    try:
                        chunk = await process.stdout.receive()
                    except anyio.EndOfStream:
                        break
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    yield line.decode("utf-8", "replace").rstrip("\r")
            if pending:
                yield pending.decode("utf-8", "replace")
            with _limit(deadline):
                code = await process.wait()
            if code != 0:
                errors.seek(0)
                last = next(
                    (line for line in reversed(errors.read().decode("utf-8", "replace").splitlines()) if line.strip()),
                    "",
                )
                raise AssistantError(
                    f"{Path(command[0]).name} stopped with exit code {code}: {last.strip()[:ERROR_LINE_CHARS]}"
                )
        finally:
            await _stop(process)


def run_command(
    command: Sequence[str], cwd: Path, environment: Mapping[str, str], timeout_seconds: float, stdin: str = ""
) -> tuple[int, str]:
    """Runs a short command (a sign-in check) and returns its exit code and output."""
    try:
        process = subprocess.Popen(  # noqa: S603  # the command is a resolved provider program and fixed arguments
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
        )
    except OSError as error:
        raise AssistantError(f"{Path(command[0]).name} couldn't start ({type(error).__name__}).") from error
    with process:
        try:
            stdout, _stderr = process.communicate(stdin, timeout=timeout_seconds)
        except BaseException as error:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            if not isinstance(error, subprocess.TimeoutExpired):
                raise
            raise AssistantError(
                f"{Path(command[0]).name} didn't answer within {timeout_seconds:g} seconds."
            ) from error
    return process.returncode, stdout


@contextmanager
def _limit(deadline: float) -> Iterator[None]:
    """A deadline for one await, entered and left without a yield in between (safe in an async generator)."""
    try:
        with anyio.fail_after(max(deadline - anyio.current_time(), 0)):
            yield
    except TimeoutError as error:
        raise AssistantError("The provider's program passed its time limit and was stopped.") from error


async def _stop(process: Process) -> None:
    with anyio.CancelScope(shield=True):
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
