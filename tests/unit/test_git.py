"""The git runner: a git stopped early gets the chance to remove its lock files."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from codetrail.repo import git as git_module
from codetrail.repo.git import run_git


def stop_once(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Makes the next communicate() raise SystemExit, as a hangup or terminate signal does, once `path` exists."""

    def stopped(
        process: subprocess.Popen[bytes], input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert path.exists(), f"{path.name} never appeared"  # raised out of run_git in place of the SystemExit
        raise SystemExit(1)

    monkeypatch.setattr(subprocess.Popen, "communicate", stopped)


def test_a_git_stopped_while_holding_the_index_lock_removes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A lock left behind would make every later commit or reset in that repository fail until removed by hand.
    work_tree = tmp_path / "guide"
    run_git(["init", "-q", str(work_tree)])
    git_dir = work_tree / ".git"
    # update-index takes the lock before reading its standard input, which the stand-in communicate() never writes.
    stop_once(git_dir / "index.lock", monkeypatch)
    with pytest.raises(SystemExit):
        run_git(["update-index", "--add", "--stdin"], git_dir=git_dir, work_tree=work_tree, input=b"page.md\n")
    assert not (git_dir / "index.lock").exists()


def test_a_git_that_ignores_the_terminate_signal_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "pid"
    stand_in = tmp_path / "git"
    stand_in.write_text(f"#!/bin/sh\ntrap '' TERM\necho $$ > '{pid_file}.tmp'\nmv '{pid_file}.tmp' '{pid_file}'\n"
                        "exec sleep 30\n")  # fmt: skip
    stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(git_module, "STOP_WAIT_SECONDS", 0.2)
    stop_once(pid_file, monkeypatch)
    started = time.monotonic()
    with pytest.raises(SystemExit):
        run_git(["status"])
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
