"""`codetrail serve`: the page on 127.0.0.1 only, opened through a single-use sign-in link (design section 7.4)."""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass

import uvicorn
from starlette.types import ASGIApp

from codetrail.config import Paths, check_containment, load_global, load_target
from codetrail.web.app import create_app
from codetrail.web.security import SessionState

HOST = "127.0.0.1"  # never configurable


@dataclass(frozen=True)
class Server:
    app: ASGIApp
    host: str
    port: int
    url: str


def prepare_server(paths: Paths, name: str) -> Server:
    target = load_target(paths, name)
    check_containment(paths, target.repository)
    settings = load_global(paths)
    session = SessionState(settings.server.login_code_ttl_seconds, session_minutes=settings.server.session_minutes)
    app = create_app(paths, name, session, settings)
    port = settings.server.port
    return Server(app, HOST, port, f"http://{HOST}:{port}/login?code={session.issue_login_code()}")


def serve(paths: Paths, name: str, open_browser: bool) -> None:
    server = prepare_server(paths, name)
    print(f"Codetrail is serving {name} at http://{server.host}:{server.port}/", flush=True)
    print(f"Sign in (this link works once, for a short time): {server.url}", flush=True)
    if open_browser:
        webbrowser.open(server.url)
    # No access log: the sign-in link's code would be written to it.
    uvicorn.run(server.app, host=server.host, port=server.port, log_level="warning", access_log=False)
