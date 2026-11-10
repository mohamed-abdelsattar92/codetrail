"""`codetrail team serve`: a team's guides on one server, under one login (design section 24.7).

The plain team page at `/` lists each repository the team follows with its free "you're behind" signal, and each
repository's own page is mounted under `/repos/<target>/`. One security middleware wraps it all, so the token,
`Origin`, session and `Sec-Fetch-Site` rules apply under a prefix as at `/`. The server holds the team's in-use lock,
and every served target's, for as long as it runs.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from collections.abc import AsyncIterator, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp

from codetrail.config import GlobalConfig, Paths, TargetConfig, TeamConfig, load_global, load_team
from codetrail.errors import CodetrailError
from codetrail.hosting import HostingService
from codetrail.hosting.names import Service
from codetrail.hosting.services import services_for
from codetrail.lock import target_in_use, team_in_use
from codetrail.repo.signal import Signal, behind
from codetrail.server import HOST
from codetrail.team.follow import follows
from codetrail.team.settings import team_targets
from codetrail.team.sync import TeamSync, sync_team
from codetrail.web.app import create_target_app, template_environment
from codetrail.web.i18n import installed_languages
from codetrail.web.security import SecurityMiddleware, SessionState, login_response

LOGIN_NEXT = ("/", "/team/add")  # where the sign-in link may lead; anything else leads to the team page
SERVICE_NAMES: dict[Service, str] = {"github": "GitHub", "azure_devops": "Azure DevOps", "gitlab": "GitLab"}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Row:
    """One followed repository on the team page."""

    target: str
    repository: str  # the name the service gives it
    branch: str
    signal: Signal | None = None
    reason: str = ""  # why its signal couldn't be read


class TimedCache[Value]:
    """Values by key, each kept for `seconds`. One computation per key runs at a time: a second page waits for the
    first's answer instead of fetching again, which would find the target's lock taken and read as "Updating"."""

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._values: dict[str, tuple[float, Value]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def get(self, key: str, compute: Callable[[], Value]) -> Value:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            found = self._values.get(key)
            if found is not None and time.monotonic() - found[0] < self._seconds:
                return found[1]
            value = compute()
            self._values[key] = (time.monotonic(), value)
            return value


def create_team_app(
    paths: Paths,
    name: str,
    session: SessionState,
    settings: GlobalConfig,
    services: Mapping[Service, HostingService],
    hold: Callable[[str], None],
    locales: Path | None = None,
) -> ASGIApp:
    """The team page, `/login` and the shared static files at the root, and each followed target's page under
    `/repos/<target>/`, all inside one security middleware.

    `hold(target)` takes the target's in-use lock before its page is mounted, at the start and whenever the team
    page finds a newly followed target; a target whose lock can't be taken isn't served."""
    mounted: set[str] = set()  # the security middleware reads it on each request
    stops: list[Callable[[], None]] = []
    mount_lock = threading.Lock()
    languages = installed_languages(locales)
    language = languages.get(settings.ui.default_language) or languages["en"]
    _ = language.translations.gettext
    environment = template_environment(str(files("codetrail.web").joinpath("templates")), language)
    signals: TimedCache[tuple[Signal | None, str]] = TimedCache(settings.signal.cache_seconds)
    syncs: TimedCache[TeamSync] = TimedCache(settings.signal.cache_seconds)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:  # a mount doesn't run its page's lifespan, so each page's update is stopped here
            with mount_lock:
                stopping = list(stops)
            for stop in stopping:
                stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def mount(target: str) -> None:
        """Holds and mounts the target's page, once. Its name joins `mounted` before its route exists, so a request
        that reaches the page is always checked as the target's (its token reads need the token)."""
        with mount_lock:
            if target in mounted:
                return
            try:
                hold(target)
            except CodetrailError as error:  # being removed, say: it isn't served
                logger.warning("Not serving target %r: %s", target, error)
                return
            target_app, stop = create_target_app(paths, target, session, settings, locales)
            stops.append(stop)
            mounted.add(target)
            app.mount(f"/repos/{target}", target_app)

    def followed(team: TeamConfig) -> dict[str, TargetConfig]:
        return {
            target: config
            for target, config in sorted(team_targets(paths, name).items())
            if config.remote is not None
            and follows(team, config.remote.service, config.remote.source, config.remote.id)
        }

    def sync(team: TeamConfig) -> TeamSync:
        """Lists each source again: newly active repositories become targets, and gone ones are reported."""
        try:
            return sync_team(paths, name, services, datetime.now(UTC))
        except CodetrailError as error:
            logger.warning("Couldn't list team %r's sources: %s", name, error)
            return TeamSync(unreachable=[f"{source.service}:{source.source}" for source in team.sources])

    def read_signal(target: str) -> tuple[Signal | None, str]:
        try:
            return behind(paths, target), ""
        except CodetrailError as error:  # its messages are Codetrail's own, never git's
            return None, str(error)
        except Exception as error:  # one repository mustn't stop the page; details stay out of it
            logger.warning("Couldn't read target %r's signal: %s", target, type(error).__name__)
            return None, _("Couldn't read this repository (%(error)s).") % {"error": type(error).__name__}

    def row(target: str, config: TargetConfig) -> Row:
        assert config.remote is not None  # only followed targets have rows
        signal, reason = signals.get(target, lambda: read_signal(target))
        return Row(target, config.remote.name, config.branch, signal, reason)

    @app.get("/login")
    def login(code: str = "", next_path: Annotated[str, Query(alias="next")] = "/") -> Response:
        return login_response(session, code, next_path if next_path in LOGIN_NEXT else "/")

    @app.get("/", response_class=HTMLResponse)
    def team_page() -> Response:
        try:
            team = load_team(paths, name)
        except CodetrailError as error:
            return PlainTextResponse(str(error), 500)
        synced = syncs.get("", lambda: sync(team))
        targets = followed(team)
        for target in targets:
            mount(target)
        served = {target: config for target, config in targets.items() if target in mounted}
        current = [target for target in served if target not in synced.stopped]
        with ThreadPoolExecutor(max_workers=settings.hosting.concurrency) as pool:
            rows = list(pool.map(lambda target: row(target, served[target]), current))
        stopped = [Row(target, config.remote.name, config.branch) for target, config in served.items()
                   if target in synced.stopped and config.remote is not None]  # fmt: skip
        html = environment.get_template("team.html").render(
            language=language, team=name, sources=team.sources, unreachable=set(synced.unreachable),
            service_names=SERVICE_NAMES, rows=rows, stopped=stopped,
        )  # fmt: skip
        return HTMLResponse(html)

    app.mount("/static", StaticFiles(directory=str(files("codetrail.web").joinpath("static"))), name="static")
    for target in followed(load_team(paths, name)):
        mount(target)
    return SecurityMiddleware(app, session=session, port=settings.server.port, mounted_targets=mounted)


def serve_team(paths: Paths, name: str, open_browser: bool, start_path: str = "/") -> None:
    """Serves the team on 127.0.0.1, holding its in-use lock and each served target's until it stops, so neither
    `team remove` nor `target remove` runs under it. The sign-in link leads to `start_path`."""
    with ExitStack() as held:
        held.enter_context(team_in_use(paths, name))
        settings = load_global(paths)
        session = SessionState(settings.server.login_code_ttl_seconds, session_minutes=settings.server.session_minutes)

        def hold(target: str) -> None:
            held.enter_context(target_in_use(paths, target))

        app = create_team_app(paths, name, session, settings, services_for(settings), hold)
        port = settings.server.port
        url = f"http://{HOST}:{port}/login?code={session.issue_login_code()}"
        if start_path != "/":
            url += f"&next={start_path}"
        print(f"Codetrail is serving team {name} at http://{HOST}:{port}/", flush=True)
        print(f"Sign in (this link works once, for a short time): {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        # No access log: the sign-in link's code would be written to it.
        uvicorn.run(app, host=HOST, port=port, log_level="warning", access_log=False)
