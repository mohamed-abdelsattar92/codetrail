"""A hangup or terminate signal stops Codetrail with what it started (design section 15.5).

Programs run in their own session, so the terminal's hangup and a signal to Codetrail's process group don't reach
them; Codetrail must turn the signal into an exception so its cleanup stops them.
"""

import http.cookiejar
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

from codetrail.web.security import TOKEN_HEADER
from tests.fixtures.repos import make_repository


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


def serve_an_update(tmp_path: Path) -> tuple[subprocess.Popen[bytes], Path, Path]:
    """`codetrail serve` running an update from the page, with a Claude Code whose paid call starts a `sleep`."""
    pid_file = tmp_path / "child.pid"
    signed_in = '{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "max"}'
    claude = tmp_path / "bin" / "fake-claude"
    claude.parent.mkdir()
    claude.write_text(
        f"#!/bin/sh\nif [ \"$1\" = auth ]; then echo '{signed_in}'; exit 0; fi\n"
        f'sleep 60 & echo $! > "{pid_file}"; wait\n'
    )
    claude.chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "config" / "codetrail" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'[server]\nport = {port}\n[providers.claude_code]\ncommand = "{claude}"\n')
    environment = os.environ | {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    checkout = make_repository(tmp_path / "target", [{"app/main.py": "x = 1\n"}])
    subprocess.run([sys.executable, "-m", "codetrail", "target", "add", "t", str(checkout)], env=environment,
                   check=True, capture_output=True)  # fmt: skip
    codetrail = subprocess.Popen(
        [sys.executable, "-m", "codetrail", "serve", "t", "--no-browser"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert codetrail.stdout is not None
    sign_in = ""
    while not sign_in.startswith("http://"):
        line = codetrail.stdout.readline().decode()
        assert line, "codetrail serve stopped before printing its sign-in link"
        sign_in = line.rpartition(": ")[2].strip()
    origin = f"http://127.0.0.1:{port}"
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    for _ in range(100):  # until uvicorn listens
        try:
            page = browser.open(sign_in).read().decode()
            break
        except urllib.error.URLError:
            time.sleep(0.05)
    token = re.search(r'name="codetrail-token" content="([^"]+)"', page)
    assert token is not None
    headers = {"origin": origin, TOKEN_HEADER: token.group(1), "content-type": "application/json"}

    def call(method: str, path: str, body: object = None) -> dict[str, object]:
        data = None if body is None else json.dumps(body).encode()
        # The served origin, http://127.0.0.1 on the port the test chose.
        request = urllib.request.Request(origin + path, data=data, headers=headers, method=method)  # noqa: S310
        return dict(json.loads(browser.open(request).read()))

    call("POST", "/update", {})
    deadline = time.monotonic() + 60
    status = call("GET", "/update/status")
    while status["state"] != "waiting" and time.monotonic() < deadline:
        assert status["state"] in ("preparing", "running"), status
        time.sleep(0.1)
        status = call("GET", "/update/status")
    call("POST", "/update/confirm", {"estimate_id": status["estimate_id"]})
    while not (pid_file.exists() and pid_file.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pid_file.exists(), "the update's assistant call never started"
    return codetrail, pid_file, tmp_path / "data" / "codetrail" / "t" / "guide"


@pytest.mark.parametrize("signal_number", [signal.SIGINT, signal.SIGHUP, signal.SIGTERM])
def test_stopping_the_server_stops_an_update_started_from_the_page(tmp_path: Path, signal_number: int) -> None:
    codetrail, pid_file, guide = serve_an_update(tmp_path)
    codetrail.send_signal(signal_number)
    codetrail.communicate(timeout=20)
    assert_stopped(pid_file)
    changes = subprocess.run(["git", "status", "--porcelain"], cwd=guide, capture_output=True, text=True, check=True)
    assert changes.stdout == ""
