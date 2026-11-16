"""Serves a repository over git's dumb HTTP protocol on 127.0.0.1, so remote mirrors can be tested without a network."""

from __future__ import annotations

import functools
import socket
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from codetrail.config import Paths, RemoteSettings, write_remote_target
from codetrail.repo.remote import refresh_remote_mirror
from tests.fixtures.repos import git

HOST = "127.0.0.1"


class QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *arguments: object) -> None:
        pass


class SignInRequiredHandler(BaseHTTPRequestHandler):
    """Answers every request with 401, as a service does for a reader who isn't signed in."""

    def do_GET(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="test"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *arguments: object) -> None:
        pass


@contextmanager
def serving(handler: Callable[..., BaseHTTPRequestHandler]) -> Iterator[str]:
    """Runs the handler on a free port in a thread and yields the server's base URL."""
    server = ThreadingHTTPServer((HOST, 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{HOST}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def publish(repository: Path, folder: Path) -> None:
    """Makes `folder/served.git` a bare copy of the repository's branches and tags, readable over dumb HTTP."""
    bare = folder / "served.git"
    if not bare.exists():
        folder.mkdir(parents=True, exist_ok=True)
        git(folder, "init", "-q", "--bare", "served.git")
    git(bare, "fetch", "-q", "--prune", "--force", str(repository), "+refs/heads/*:refs/heads/*",
        "+refs/tags/*:refs/tags/*")  # fmt: skip
    git(bare, "update-server-info")


@contextmanager
def serving_repository(repository: Path, folder: Path) -> Iterator[str]:
    """Serves the repository (publish it again after new commits) and yields the URL of its bare copy."""
    publish(repository, folder)
    with serving(functools.partial(QuietFileHandler, directory=str(folder))) as base_url:
        yield f"{base_url}/served.git"


def allow_http_remotes(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    """Lets a remote target point at a server on 127.0.0.1: the host check passes and git may use plain http.

    Only code can widen the protocols and the hosts, so the tests that need a local server do it by patching.
    """
    hermetic_home(monkeypatch, folder / "home")
    monkeypatch.setattr("codetrail.config.check_clone_url", lambda service, url: url)
    monkeypatch.setattr("codetrail.repo.remote.check_clone_url", lambda service, url: url)
    monkeypatch.setattr(
        "codetrail.repo.remote.refresh_remote_mirror",
        functools.partial(refresh_remote_mirror, protocols=("http",)),
    )


def write_served_target(paths: Paths, name: str, url: str, branch: str = "develop") -> None:
    remote = RemoteSettings(service="github", source="acme-org", id="R_1", name="web-shop", url=url)
    write_remote_target(paths, name, remote, branch, "acme")


def hermetic_home(monkeypatch: pytest.MonkeyPatch, folder: Path) -> Path:
    """Points HOME at an empty folder, so the developer's git configuration and credential helpers aren't read."""
    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(folder / ".config"))
    return folder


@contextmanager
def stalling_server() -> Iterator[str]:
    """Accepts connections (the kernel does, from its backlog) and never answers; yields the base URL."""
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        listener.listen(5)
        yield f"http://{HOST}:{listener.getsockname()[1]}"


def within[Result](seconds: float, function: Callable[[], Result]) -> Result:
    """The function's result; a call still running after `seconds` fails the test instead of hanging it."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        return executor.submit(function).result(timeout=seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
