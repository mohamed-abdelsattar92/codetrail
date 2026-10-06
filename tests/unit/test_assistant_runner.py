"""Running a provider's program: stdin in, lines out, bounded in time, and nothing left running (design 15.5)."""

import os
import sys
import time
from contextlib import aclosing
from pathlib import Path

import pytest

from codetrail.assistant import AssistantError
from codetrail.assistant.runner import run_command, run_program

ECHO = "import sys\nfor line in sys.stdin.read().splitlines():\n    print('got ' + line, flush=True)\n"
FAIL = "import sys\nprint('partial', flush=True)\nsys.stderr.write('first\\nlast words here\\n')\nsys.exit(3)\n"
SLOW_CHILD = (
    "import os, subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "print('started', flush=True)\n"
    "time.sleep(60)\n"
)
NOISY = "import sys\nsys.stderr.write('x' * 1_000_000)\nprint('done', flush=True)\n"


def script(tmp_path: Path, name: str, body: str) -> list[str]:
    path = tmp_path / name
    path.write_text(body)
    return [sys.executable, str(path)]


async def collect(command: list[str], stdin: str, tmp_path: Path, timeout: float = 20) -> list[str]:
    return [line async for line in run_program(command, stdin, tmp_path, {"PATH": "/usr/bin:/bin"}, timeout)]


@pytest.mark.anyio
async def test_the_prompt_goes_in_on_stdin_and_lines_come_back(tmp_path: Path) -> None:
    lines = await collect(script(tmp_path, "echo.py", ECHO), "one\ntwo \uff20 three\n", tmp_path)
    assert lines == ["got one", "got two \uff20 three"]


@pytest.mark.anyio
async def test_a_failing_program_raises_with_its_last_error_line(tmp_path: Path) -> None:
    with pytest.raises(AssistantError, match=r"exit code 3.*last words here"):
        await collect(script(tmp_path, "fail.py", FAIL), "", tmp_path)


@pytest.mark.anyio
async def test_a_program_that_runs_too_long_is_stopped_with_its_children(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    with pytest.raises(AssistantError, match="time limit"):
        await collect([*script(tmp_path, "slow.py", SLOW_CHILD), str(pid_file)], "", tmp_path, timeout=2)
    assert time.monotonic() - started < 10
    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


@pytest.mark.anyio
async def test_a_reader_that_stops_early_stops_the_program(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    command = [*script(tmp_path, "slow.py", SLOW_CHILD), str(pid_file)]
    async with aclosing(run_program(command, "", tmp_path, {"PATH": "/usr/bin:/bin"}, 20)) as lines:
        async for _line in lines:
            break
    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


@pytest.mark.anyio
async def test_lots_of_error_output_doesnt_block_the_program(tmp_path: Path) -> None:
    assert await collect(script(tmp_path, "noisy.py", NOISY), "", tmp_path) == ["done"]


def test_a_short_command_returns_its_output(tmp_path: Path) -> None:
    command = script(tmp_path, "echo.py", ECHO)
    assert run_command(command, tmp_path, {"PATH": "/usr/bin:/bin"}, 10, stdin="a\n") == (0, "got a\n")


def test_a_short_command_that_runs_too_long_is_stopped_with_its_children(tmp_path: Path) -> None:
    # Stopping only the program would leave what it started running.
    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    with pytest.raises(AssistantError, match="didn't answer within 2 seconds"):
        run_command([*script(tmp_path, "slow.py", SLOW_CHILD), str(pid_file)], tmp_path, {"PATH": "/usr/bin:/bin"}, 2)
    assert time.monotonic() - started < 10
    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
