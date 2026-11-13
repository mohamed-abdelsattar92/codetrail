"""Running a provider's program: stdin in, lines out, bounded in time, and nothing left running (design 15.5)."""

import os
import sys
import time
from contextlib import aclosing
from pathlib import Path

import pytest

from codetrail.assistant import AssistantError, runner
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
# Writes without end to one stream, with a child in its process group that must be stopped with it.
ENDLESS = (
    "import subprocess, sys\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "stream = getattr(sys, sys.argv[2]).buffer\n"
    "while True:\n"
    "    stream.write(b'x' * 65536)\n"
    "    stream.flush()\n"
)
LONG_LINE = "import sys\nsys.stdout.write('y' * 3_000_000 + '\\nend\\n')\n"


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


def test_a_short_command_gets_no_input(tmp_path: Path) -> None:
    command = script(tmp_path, "echo.py", ECHO)
    assert run_command(command, tmp_path, {"PATH": "/usr/bin:/bin"}, 10) == (0, "")  # it gets no input


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


def assert_stopped(pid_file: Path) -> None:
    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


@pytest.mark.anyio
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
async def test_a_program_that_writes_past_the_output_limit_is_stopped_with_its_children(
    tmp_path: Path, stream: str
) -> None:
    pid_file = tmp_path / "child.pid"
    command = [*script(tmp_path, "endless.py", ENDLESS), str(pid_file), stream]
    started = time.monotonic()
    with pytest.raises(AssistantError, match="returned more than 100000 bytes"):
        async for _line in run_program(command, "", tmp_path, {"PATH": "/usr/bin:/bin"}, 60, max_output_bytes=100_000):
            pass
    assert time.monotonic() - started < 10
    assert_stopped(pid_file)


@pytest.mark.anyio
async def test_a_line_longer_than_a_read_comes_back_whole(tmp_path: Path) -> None:
    lines = await collect(script(tmp_path, "long.py", LONG_LINE), "", tmp_path)
    assert lines == ["y" * 3_000_000, "end"]


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_a_short_command_that_writes_past_the_output_limit_is_stopped_with_its_children(
    tmp_path: Path, stream: str
) -> None:
    pid_file = tmp_path / "child.pid"
    command = [*script(tmp_path, "endless.py", ENDLESS), str(pid_file), stream]
    started = time.monotonic()
    with pytest.raises(AssistantError, match="returned more than 100000 bytes"):
        run_command(command, tmp_path, {"PATH": "/usr/bin:/bin"}, 60, error_output=[], max_output_bytes=100_000)
    assert time.monotonic() - started < 10
    assert_stopped(pid_file)


def test_a_short_command_keeps_its_error_output_apart(tmp_path: Path) -> None:
    errors: list[str] = []
    code, output = run_command(
        script(tmp_path, "fail.py", FAIL), tmp_path, {"PATH": "/usr/bin:/bin"}, 10, error_output=errors
    )
    assert (code, output, errors) == (3, "partial\n", ["first\nlast words here\n"])


def test_a_short_command_can_merge_its_error_output(tmp_path: Path) -> None:
    code, output = run_command(
        script(tmp_path, "fail.py", FAIL), tmp_path, {"PATH": "/usr/bin:/bin"}, 10, include_stderr=True
    )
    assert code == 3
    assert output == "partial\nfirst\nlast words here\n"


def refuse_to_start_after(pid_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes the output readers fail to start once the program has started its child."""

    def start(reader: object) -> None:
        for _ in range(500):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.01)
        raise RuntimeError("can't start a new thread")

    monkeypatch.setattr(runner.OutputReader, "start", start)


def open_descriptors() -> int:
    return len(os.listdir("/dev/fd"))


@pytest.mark.anyio
async def test_a_reader_that_fails_to_start_stops_the_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "child.pid"
    command = [*script(tmp_path, "slow.py", SLOW_CHILD), str(pid_file)]
    refuse_to_start_after(pid_file, monkeypatch)
    before = open_descriptors()
    with pytest.raises(RuntimeError, match="new thread"):
        await collect(command, "", tmp_path)
    assert_stopped(pid_file)
    assert open_descriptors() == before


def test_a_short_command_s_reader_that_fails_to_start_stops_the_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "child.pid"
    refuse_to_start_after(pid_file, monkeypatch)
    before = open_descriptors()
    with pytest.raises(RuntimeError, match="new thread"):
        run_command([*script(tmp_path, "slow.py", SLOW_CHILD), str(pid_file)], tmp_path, {"PATH": "/usr/bin:/bin"}, 20)
    assert_stopped(pid_file)
    assert open_descriptors() == before
