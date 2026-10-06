"""A hangup or terminate signal stops Codetrail with what it started (design section 15.5).

Programs run in their own session, so the terminal's hangup and a signal to Codetrail's process group don't reach
them; Codetrail must turn the signal into an exception so its cleanup stops them.
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest


def start_codetrail(
    tmp_path: Path, preexec: Callable[[], object] | None = None
) -> tuple[subprocess.Popen[bytes], Path]:
    """`codetrail providers` with a Claude Code whose sign-in check starts a background `sleep` and waits."""
    pid_file = tmp_path / "child.pid"
    claude = tmp_path / "bin" / "fake-claude"
    claude.parent.mkdir()
    claude.write_text(f'#!/bin/sh\nsleep 60 & echo $! > "{pid_file}"; wait\n')
    claude.chmod(0o755)
    config = tmp_path / "config" / "codetrail" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f'[providers.claude_code]\ncommand = "{claude}"\n[providers.codex]\ncommand = "/nonexistent/codex"\n'
        '[providers.local]\nbase_url = "http://127.0.0.1:9/v1"\n'
    )
    environment = os.environ | {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    codetrail = subprocess.Popen(
        [sys.executable, "-m", "codetrail", "providers"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environment,
        preexec_fn=preexec,  # only sets a signal disposition before exec
    )
    deadline = time.monotonic() + 20
    while not (pid_file.exists() and pid_file.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pid_file.exists(), "the sign-in check never started"
    return codetrail, pid_file


def assert_stopped(pid_file: Path) -> None:
    child = int(pid_file.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


@pytest.mark.parametrize("signal_number", [signal.SIGHUP, signal.SIGTERM])
def test_a_hangup_or_terminate_signal_stops_what_codetrail_started(tmp_path: Path, signal_number: int) -> None:
    codetrail, pid_file = start_codetrail(tmp_path)
    codetrail.send_signal(signal_number)
    _output, errors = codetrail.communicate(timeout=10)
    assert codetrail.returncode == 128 + signal_number, errors.decode()
    assert_stopped(pid_file)


def test_a_hangup_ignored_at_start_stays_ignored(tmp_path: Path) -> None:
    # Started with nohup, Codetrail keeps running when the terminal closes.
    codetrail, pid_file = start_codetrail(tmp_path, lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    codetrail.send_signal(signal.SIGHUP)
    time.sleep(0.5)
    assert codetrail.poll() is None
    codetrail.send_signal(signal.SIGTERM)
    codetrail.communicate(timeout=10)
    assert_stopped(pid_file)
