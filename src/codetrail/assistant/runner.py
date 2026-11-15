"""Runs a provider's program: the prompt on stdin, its output as lines, bounded in time and size (design section 15.5).

Each program runs in its own process group, which is killed when the call ends for any reason (done, failed, timed out,
wrote too much, or the reader stopped listening), so nothing it started keeps running. Its input and output pipes are
closed then too, even while a child that left the group still holds their other ends. Its output is read as it comes,
and its error output in a thread, so a talkative program can't fill a pipe and stall; once either passes
`max_output_bytes`, the program is killed and the call fails, so its output is never used. One race remains, and is
harmless: a program that writes too much error output and exits cleanly before the reader stops it succeeds, since its
error output is read only to explain a failure and what passed the limit is never kept.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO

import anyio
from anyio.abc import Process
from anyio.lowlevel import checkpoint

from codetrail.assistant import AssistantError

ERROR_LINE_CHARS = 300
MAX_OUTPUT_BYTES = 50_000_000  # of a provider program's output, and of its error output, each
READ_BYTES = 65_536


async def run_program(
    command: Sequence[str],
    stdin_text: str,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    *,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> AsyncGenerator[str]:
    """Yields the program's output lines; raises AssistantError on a failure exit, when the time limit passes, or
    when the program writes more than `max_output_bytes`."""
    deadline = anyio.current_time() + timeout_seconds
    name = Path(command[0]).name
    errors_in, errors_out = os.pipe()
    try:
        process = await anyio.open_process(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors_out,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
        )
    except BaseException:
        os.close(errors_in)
        raise
    finally:
        os.close(errors_out)
    try:
        errors = OutputReader(open(errors_in, "rb"), process.pid, max_output_bytes)  # noqa: SIM115  # the reader closes it
        assert process.stdin is not None and process.stdout is not None
        with _limit(deadline):
            await process.stdin.send(stdin_text.encode("utf-8"))
            await process.stdin.aclose()
        size = 0
        pending: list[bytes] = []  # the start of a line not yet ended, in pieces: joined once, when it ends
        while True:
            with _limit(deadline):
                try:
                    chunk = await process.stdout.receive()
                except anyio.EndOfStream:
                    break
            size += len(chunk)
            if size > max_output_bytes:
                raise too_much_output(name, max_output_bytes)
            *ended, rest = chunk.split(b"\n")
            if ended:
                ended[0] = b"".join([*pending, ended[0]])
                pending = []
                for line in ended:
                    yield line.decode("utf-8", "replace").rstrip("\r")
            if rest:
                pending.append(rest)
        if pending:
            yield b"".join(pending).decode("utf-8", "replace")
        with _limit(deadline):
            code = await process.wait()
        if code != 0:
            _kill(process.pid)  # so nothing left in its group holds the error output open
            await anyio.to_thread.run_sync(errors.join, max(deadline - anyio.current_time(), 0))
            if errors.over:
                raise too_much_output(name, max_output_bytes)
            last = next((line for line in reversed(errors.text().splitlines()) if line.strip()), "")
            raise AssistantError(f"{name} stopped with exit code {code}: {last.strip()[:ERROR_LINE_CHARS]}")
    finally:
        await _stop(process)


def run_command(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    *,
    include_stderr: bool = False,
    error_output: list[str] | None = None,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> tuple[int, str]:
    """Runs a short command (a sign-in check, a hosting read), with no input, and returns its exit code and output. With
    `include_stderr`, the error output is part of the output (Codex's sign-in check writes there); with
    `error_output`, the error output is appended to that list instead. Writing more than `max_output_bytes` to
    either stream stops the command and raises AssistantError."""
    name = Path(command[0]).name
    deadline = time.monotonic() + timeout_seconds
    try:
        process = subprocess.Popen(  # noqa: S603  # the command is a resolved program and fixed arguments
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if include_stderr else subprocess.PIPE,
            cwd=cwd,
            env=dict(environment),
            start_new_session=True,
        )
    except OSError as error:
        raise AssistantError(f"{name} couldn't start ({type(error).__name__}).") from error
    streams = [stream for stream in (process.stdout, process.stderr) if stream is not None]
    readers: list[OutputReader] = []
    try:
        for stream in streams:
            readers.append(OutputReader(stream, process.pid, max_output_bytes))
        process.wait(timeout_seconds)
        for reader in readers:
            reader.join(max(deadline - time.monotonic(), 0))
        if any(reader.is_alive() for reader in readers):  # something it started still holds its output open
            raise subprocess.TimeoutExpired(command, timeout_seconds)
    except BaseException as error:
        _kill(process.pid)
        process.wait()
        for stream in streams[len(readers) :]:
            stream.close()  # no reader started for it, so no thread reads it
        if not isinstance(error, subprocess.TimeoutExpired):
            raise
        raise AssistantError(f"{name} didn't answer within {timeout_seconds:g} seconds.") from error
    finally:
        _kill(process.pid)  # what it started goes with it
    if any(reader.over for reader in readers):
        raise too_much_output(name, max_output_bytes)
    if error_output is not None and len(readers) > 1:
        error_output.append(readers[1].text())
    return process.returncode, readers[0].text()


class OutputReader(threading.Thread):
    """Reads one of a program's output streams to its end, keeping it in memory up to `limit` bytes. Past the limit
    it kills the program's process group and stops reading."""

    def __init__(self, stream: IO[bytes], process_id: int, limit: int) -> None:
        super().__init__(daemon=True)
        self.stream, self.process_id, self.limit = stream, process_id, limit
        self.chunks: list[bytes] = []
        self.size = 0
        try:
            self.start()
        except BaseException:
            stream.close()
            raise

    @property
    def over(self) -> bool:
        return self.size > self.limit

    def run(self) -> None:
        with self.stream:
            while chunk := os.read(self.stream.fileno(), READ_BYTES):
                self.size += len(chunk)
                if self.over:
                    _kill(self.process_id)
                    return
                self.chunks.append(chunk)

    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8", "replace")


def too_much_output(name: str, limit: int) -> AssistantError:
    return AssistantError(f"{name} returned more than {limit} bytes, so it was stopped and its output isn't used.")


@contextmanager
def _limit(deadline: float) -> Iterator[None]:
    """A deadline for one await, entered and left without a yield in between (safe in an async generator)."""
    try:
        with anyio.fail_after(max(deadline - anyio.current_time(), 0)):
            yield
    except TimeoutError as error:
        raise AssistantError("The provider's program passed its time limit and was stopped.") from error


def _kill(process_id: int) -> None:
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process_id, signal.SIGKILL)


async def _stop(process: Process) -> None:
    """Kills the program's group and closes its input and output pipes. Waiting returns once the program exits, but a
    pipe closes by itself only once nothing else holds its other end, and a child that left the group can."""
    with anyio.CancelScope(shield=True):
        _kill(process.pid)
        await process.aclose()
        await checkpoint()  # asyncio closes the pipes on its next turn
