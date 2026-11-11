"""`codetrail team serve`: a team's guides on one server, under one login (design section 24.7).

The plain team page at `/` lists each repository the team follows with its free "you're behind" signal, and each
repository's own page is mounted under `/repos/<target>/`. One security middleware wraps it all, so the token,
`Origin`, session and `Sec-Fetch-Site` rules apply under a prefix as at `/`. The server holds the team's in-use lock,
and every served target's, for as long as it runs. The sources are listed once, at the start, and the targets that
listing adds get their free first refresh. A page view writes no settings, lists no source and never clones.
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
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.types import ASGIApp

from codetrail.assistant import Assistant
from codetrail.config import TARGET_NAME, GlobalConfig, Paths, load_global, load_team
from codetrail.errors import CodetrailError
from codetrail.generate.run import Progress
from codetrail.guide import GuideRepository
from codetrail.hosting import HostingService
from codetrail.hosting.names import SERVICE_NAMES, Service
from codetrail.hosting.services import services_for
from codetrail.lock import target_in_use, team_in_use
from codetrail.repo.signal import Signal, behind
from codetrail.server import HOST
from codetrail.team.catch_up import run_catch_up
from codetrail.team.catch_up_job import CatchUpJob
from codetrail.team.catch_up_job import Confirm as CatchUpConfirm
from codetrail.team.follow import followed_targets
from codetrail.team.refresh import TeamRefreshJob
from codetrail.team.routes import team_router
from codetrail.team.sync import TeamSync, sync_team
from codetrail.web.app import EstimateDecision, LanguageChoice, UpdateJob, create_target_app, template_environment
from codetrail.web.i18n import Language, installed_languages
from codetrail.web.security import SecurityMiddleware, SessionState, login_response

LOGIN_NEXT = ("/", "/team/add")  # where the sign-in link may lead; anything else leads to the team page
logger = logging.getLogger(__name__)


class CatchUpDecision(BaseModel):
    """Go ahead with the waiting estimate, for the ticked targets only (design section 24.8)."""

    model_config = ConfigDict(extra="forbid")
    estimate_id: str = Field(max_length=100)
    targets: list[Annotated[str, StringConstraints(pattern=f"^(?:{TARGET_NAME.pattern})$")]] = Field(max_length=1000)


@dataclass(frozen=True)
class Row:
    """One followed repository on the team page."""

    target: str
    repository: str  # the name the service gives it
    branch: str
    signal: Signal | None = None
    reason: str = ""  # why its signal couldn't be read, or why it isn't served
    served: bool = True  # its guide is mounted
    has_guide: bool = False  # its guide has pages: until then, its signal measures only the facts


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
    hold: Callable[[str], Callable[[], None]],
    locales: Path | None = None,
    *,
    synced: TeamSync | None = None,
    writer_for: Callable[[str], Assistant | None] = lambda target: None,
    overview_writer: Assistant | None = None,
) -> ASGIApp:
    """The team page, `/login` and the shared static files at the root, and each followed target's page under
    `/repos/<target>/`, all inside one security middleware.

    `hold(target)` takes the target's in-use lock before its page is mounted, at the start and whenever the team
    page finds a followed target it doesn't serve yet, and returns the lock's release. A target whose lock can't be
    taken isn't served, and its row says why. `synced` is the sources' listing taken at the start: the archived and
    gone repositories, and the sources that couldn't be listed; a save on the add-repositories screen replaces it.
    `services` are what that screen lists, and its saves sync. `writer_for(target)` and `overview_writer` are the
    catch-up's assistants (tests' fakes), or None for the real ones."""
    listing = synced or TeamSync()
    mounted: set[str] = set()  # the security middleware reads it on each request
    refused: dict[str, str] = {}  # target: why its lock couldn't be taken
    page_jobs: list[UpdateJob] = []  # each served page's update, which the server's lifespan stops
    mount_lock = threading.Lock()
    languages = installed_languages(locales)
    templates = str(files("codetrail.web").joinpath("templates"))
    environments = {code: template_environment(templates, language) for code, language in languages.items()}
    chosen = [settings.ui.default_language]  # the header's language picker sets it, for as long as the server runs

    def language() -> Language:
        return languages.get(chosen[0]) or languages["en"]

    def _(message: str) -> str:
        return language().translations.gettext(message)

    signals: TimedCache[tuple[Signal | None, str, bool]] = TimedCache(settings.signal.cache_seconds)
    refresh = TeamRefreshJob(paths, settings.hosting.concurrency, _, settings.server.update_log_lines)

    def catch_up_sync() -> TeamSync:
        """The catch-up lists the sources again, and serves each target that listing adds."""
        result = list_sources(paths, name, services)
        record_sync(result)
        for target in result.added:
            mount(target)
        return result

    def run(confirm: CatchUpConfirm, progress: Progress, stop: threading.Event) -> object:
        return run_catch_up(paths, name, confirm, progress, stop, catch_up_sync, writer_for, overview_writer)

    catch_up = CatchUpJob(
        run,
        settings.server.update_cooldown_seconds,
        settings.server.estimate_ttl_seconds,
        settings.server.update_log_lines,
        gettext_of=lambda: language().translations.gettext,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:  # a mount doesn't run its page's lifespan, so each page's update is stopped here
            with mount_lock:
                pages = list(page_jobs)
            # Every job stops starting paid work before any is waited for, and an interrupt during one wait (a
            # signal) never skips another's (design section 15.5).
            for request_stop in [catch_up.request_stop, refresh.request_stop, *(page.request_stop for page in pages)]:
                request_stop()
            wait_for_each([catch_up.wait_for_stop, refresh.wait_for_stop, *(page.wait_for_stop for page in pages)])

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def mount(target: str) -> None:
        """Holds and mounts the target's page, once. Its name joins `mounted` before its route exists, so a request
        that reaches the page is always checked as the target's (its token reads need the token)."""
        with mount_lock:
            if target in mounted:
                return
            try:
                release = hold(target)
            except CodetrailError as error:  # being removed, say: it isn't served, and its row says why
                logger.warning("Not serving target %r: %s", target, error)
                refused[target] = str(error)
                return
            refused.pop(target, None)
            try:
                target_app, job = create_target_app(paths, target, session, settings, locales, team=name)
            except BaseException:
                release()  # nothing is served, so nothing stays held
                raise
            page_jobs.append(job)
            mounted.add(target)
            app.mount(f"/repos/{target}", target_app)

    def read_signal(target: str) -> tuple[Signal | None, str]:
        try:
            return behind(paths, target), ""
        except CodetrailError as error:  # its messages are Codetrail's own, never git's
            return None, str(error)
        except Exception as error:  # one repository mustn't stop the page; details stay out of it
            logger.warning("Couldn't read target %r's signal: %s", target, type(error).__name__)
            return None, _("Couldn't read this repository (%(error)s).") % {"error": type(error).__name__}

    def read_target(target: str) -> tuple[Signal | None, str, bool]:
        """The target's signal, why it couldn't be read, and whether its guide has pages yet."""
        signal, reason = read_signal(target)
        try:
            written = GuideRepository(paths.target_data(target) / "guide").has_guide_pages()
        except Exception as error:  # a page half-written, or edited by hand, mustn't stop the page either
            logger.warning("Couldn't read target %r's guide: %s", target, type(error).__name__)
            message = _("Couldn't read this repository's guide (%(error)s).") % {"error": type(error).__name__}
            return signal, reason or message, False
        return signal, reason, written

    def row(target: str, repository: str, branch: str) -> Row:
        """A page view never clones: a target waiting for its first refresh says so, as one never cloned does,
        without fetching (a fetch would clone it, and the refresh would then find it busy)."""
        if target in refused:
            return Row(target, repository, branch, reason=refused[target], served=False)
        if refresh.is_pending(target):
            return Row(target, repository, branch, Signal(updating=True))
        if not (paths.target_data(target) / "mirror.git" / "HEAD").exists():
            return Row(target, repository, branch, Signal())  # not updated yet
        signal, reason, written = signals.get(target, lambda: read_target(target))
        return Row(target, repository, branch, signal, reason, has_guide=written)

    def render(
        template: str, active: str, targets: dict[str, tuple[str, str]] | None = None, **context: Any
    ) -> HTMLResponse:
        """A team page in the guides' shell: its sidebar lists each followed repository the server serves.
        `targets` are the followed ones, as `followed_targets()` gives them, when the caller has them already."""
        if targets is None:
            try:
                targets = followed_targets(paths, name, load_team(paths, name))
            except CodetrailError:  # the page, or its script's reads, say why
                targets = {}
        current = language()
        html = environments[current.code].get_template(template).render(
            language=current, languages=list(languages.values()), token=session.token, team=name, active=active,
            team_repositories=[(target, targets[target][0]) for target in targets if target in mounted], **context,
        )  # fmt: skip
        return HTMLResponse(html)

    @app.get("/login")
    def login(code: str = "", next_path: Annotated[str, Query(alias="next")] = "/") -> Response:
        return login_response(session, code, next_path if next_path in LOGIN_NEXT else "/")

    @app.get("/", response_class=HTMLResponse)
    def team_page() -> Response:
        try:
            team = load_team(paths, name)
        except CodetrailError as error:
            return PlainTextResponse(str(error), 500)
        # A followed target this server doesn't hold yet (another command wrote it) is held and mounted: in memory
        # only. Nothing here writes settings or lists the sources.
        targets = followed_targets(paths, name, team)
        for target in targets:
            mount(target)
        current = [target for target in targets if target not in listing.stopped]
        with ThreadPoolExecutor(max_workers=settings.hosting.concurrency) as pool:
            rows = list(pool.map(lambda target: row(target, *targets[target]), current))
        stopped = [Row(target, *targets[target], served=target in mounted) for target in targets
                   if target in listing.stopped]  # fmt: skip
        return render(
            "team.html", "team", targets, sources=team.sources, unreachable=set(listing.unreachable),
            service_names=SERVICE_NAMES, rows=rows, stopped=stopped,
        )  # fmt: skip

    @app.post("/settings/language")
    def set_language(choice: LanguageChoice) -> Response:
        if choice.language not in languages:
            return Response(status_code=400)
        chosen[0] = choice.language
        return Response(status_code=204)

    def record_sync(result: TeamSync) -> None:
        nonlocal listing
        listing = result

    @app.post("/team/catch-up")
    def start_catch_up() -> Response:
        refusal = catch_up.start()
        if refusal is None:
            return JSONResponse({"state": catch_up.state, "message": ""}, status_code=202)
        return JSONResponse({"state": catch_up.state, "message": refusal}, status_code=409)

    @app.get("/team/catch-up/status")
    def catch_up_status(after: Annotated[int, Query(ge=0, le=2**53)] = 0) -> Response:
        """The catch-up's state, its steps after number `after` rendered for the panel, and, while it waits, its
        estimate rendered for the dialog, in the reader's language and autoescaped."""
        status = catch_up.status(after)
        environment = environments[language().code]
        if "estimate" in status:
            status["estimate_html"] = environment.get_template("catch_up_estimate.html").render(
                estimate=status["estimate"]
            )
        steps = status.pop("steps")
        status["log_html"] = environment.get_template("catch_up_log.html").render(steps=steps) if steps else ""
        return JSONResponse(status)

    @app.post("/team/catch-up/confirm")
    def confirm_catch_up(decision: CatchUpDecision) -> Response:
        if not catch_up.decide(decision.estimate_id, decision.targets):
            refused = _("That estimate isn't waiting any more, or doesn't list those repositories.")
            return JSONResponse({"error": refused}, 400)
        return Response(status_code=204)

    @app.post("/team/catch-up/cancel")
    def cancel_catch_up(decision: EstimateDecision) -> Response:
        if not catch_up.decide(decision.estimate_id, None):
            return JSONResponse({"error": _("That estimate isn't waiting any more.")}, 400)
        return Response(status_code=204)

    app.include_router(team_router(paths, name, settings, services, language, render, mount, refresh, record_sync))
    app.mount("/static", StaticFiles(directory=str(files("codetrail.web").joinpath("static"))), name="static")
    for target in followed_targets(paths, name, load_team(paths, name)):
        mount(target)
    if listing.added:  # the start's listing wrote them: they get the free first refresh a save starts
        refresh.start(listing.added)
    return SecurityMiddleware(app, session=session, port=settings.server.port, mounted_targets=mounted)


def wait_for_each(waits: list[Callable[[], None]]) -> None:
    """Runs every wait, even after one is interrupted, then raises the first interruption."""
    interrupted: BaseException | None = None
    for wait in waits:
        try:
            wait()
        except BaseException as error:  # raised below, once every job's cleanup ran
            interrupted = interrupted or error
    if interrupted is not None:
        raise interrupted


def list_sources(paths: Paths, name: str, services: Mapping[Service, HostingService]) -> TeamSync:
    """Lists each source once: newly active repositories become targets, and the archived and gone are reported.
    If the team can't be synced at all, every source counts as not listed."""
    try:
        return sync_team(paths, name, services, datetime.now(UTC))
    except CodetrailError as error:
        logger.warning("Couldn't list team %r's sources: %s", name, error)
        return TeamSync(unreachable=[f"{source.service}:{source.source}" for source in load_team(paths, name).sources])


def serve_team(paths: Paths, name: str, open_browser: bool, start_path: str = "/") -> None:
    """Lists the team's sources once, then serves the team on 127.0.0.1, holding its in-use lock and each served
    target's until it stops, so neither `team remove` nor `target remove` runs under it. The sign-in link leads to
    `start_path`."""
    with ExitStack() as held:
        held.enter_context(team_in_use(paths, name))
        settings = load_global(paths)
        services = services_for(settings)
        synced = list_sources(paths, name, services)
        session = SessionState(settings.server.login_code_ttl_seconds, session_minutes=settings.server.session_minutes)

        def hold(target: str) -> Callable[[], None]:
            lock = ExitStack()  # its own stack, so a page that fails to mount can release it at once
            lock.enter_context(target_in_use(paths, target))
            held.push(lock)
            return lock.close

        app = create_team_app(paths, name, session, settings, services, hold, synced=synced)
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
