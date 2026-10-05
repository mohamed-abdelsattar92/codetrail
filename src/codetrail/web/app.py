"""The local page's application: routes over one target's facts, sources and signal (design section 7.1).

Every route is behind the security middleware. Templates autoescape; interface text comes from the reader's catalog,
and guide content is marked as English.
"""

from __future__ import annotations

import difflib
import hmac
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp

from codetrail.assistant import Assistant
from codetrail.assistant.estimate import CallEstimate, UpdateEstimate, estimate_call, tokens_text, when_text
from codetrail.assistant.routing import build_assistant
from codetrail.assistant.status import require_ready
from codetrail.bridge import bridge_router
from codetrail.config import GlobalConfig, Paths, load_target, model_choice
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.facts import EntityKind
from codetrail.generate.scope import sources_changed
from codetrail.guide import PAGE_ID, GuideRepository, Page, parse_page
from codetrail.learn import LearningState, page_checks
from codetrail.learn.routes import learning_router
from codetrail.repo.signal import Signal, behind
from codetrail.update import run_update
from codetrail.web.diagrams import dependencies_diagram, imports_diagram
from codetrail.web.i18n import Language, installed_languages
from codetrail.web.render import render_body
from codetrail.web.security import SESSION_COOKIE, SecurityMiddleware, SessionState, login_response
from codetrail.web.target_view import TargetView

LANGUAGE_SETTING = "language"


class LanguageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(max_length=16)


def create_app(
    paths: Paths,
    name: str,
    session: SessionState,
    settings: GlobalConfig,
    locales: Path | None = None,
    updater: Callable[[Callable[[UpdateEstimate], bool]], object] | None = None,
    assistant_for: Callable[[], Assistant] | None = None,
) -> ASGIApp:
    """The page, wrapped in the security middleware outside everything, so every response passes through it.

    `updater` runs one update; the Update button runs it in a background thread (tests pass a fake one).
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    view = TargetView(paths, name)
    guide = GuideRepository(paths.target_data(name) / "guide")
    job = UpdateJob(
        updater or (lambda confirm: run_update(paths, name, confirm=confirm)),
        settings.server.update_cooldown_seconds,
        settings.server.estimate_ttl_seconds,
    )

    @contextmanager
    def learning() -> Iterator[LearningState]:
        connection = connect(view.data / "codetrail.db")
        try:
            yield LearningState(connection)
        finally:
            connection.close()

    def real_assistant() -> Assistant:
        target = load_target(paths, name)
        require_ready(target, settings, kinds=("answer", "grade"))
        return build_assistant(paths.target_data(name) / "source", settings, target)

    languages = installed_languages(locales)
    templates = str(files("codetrail.web").joinpath("templates"))
    environments = {code: _environment(templates, language) for code, language in languages.items()}
    signal_cache: dict[str, tuple[float, Signal | None]] = {}

    def language() -> Language:
        database = view.data / "codetrail.db"
        chosen = None
        if database.exists():
            connection = connect(database)
            try:
                row = connection.execute("SELECT value FROM settings WHERE key = ?", (LANGUAGE_SETTING,)).fetchone()
            finally:
                connection.close()
            chosen = row["value"] if row else None
        return languages.get(chosen or settings.ui.default_language) or languages["en"]

    def call_estimates() -> dict[str, CallEstimate]:
        """What a question and a graded check are expected to use, for their buttons (design section 15.4)."""
        database = view.data / "codetrail.db"
        if not database.exists():
            return {}
        target = load_target(paths, name)
        connection = connect(database)
        try:
            return {
                kind: estimate_call(connection, kind, *model_choice(getattr(target.models, kind)), settings.estimates,
                                    settings.prices)
                for kind in ("answer", "grade")
            }  # fmt: skip
        finally:
            connection.close()

    def render(template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
        current = language()
        html = (
            environments[current.code]
            .get_template(template)
            .render(language=current, languages=list(languages.values()), token=session.token, target=name,
                    estimates=call_estimates(), **context)
        )  # fmt: skip
        return HTMLResponse(html, status_code=status_code)

    def not_found() -> HTMLResponse:
        return render("error.html", status_code=404)

    def current_signal() -> Signal | None:
        cached = signal_cache.get("signal")
        if cached and time.monotonic() - cached[0] < settings.signal.cache_seconds:
            return cached[1]
        try:
            result: Signal | None = behind(paths, name)
        except CodetrailError:
            result = None
        signal_cache["signal"] = (time.monotonic(), result)
        return result

    @app.get("/login")
    def login(code: str = "") -> Response:
        return login_response(session, code)

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        counts: list[tuple[str, int]] = []
        snapshot = None
        if (view.data / "codetrail.db").exists():
            with view.store() as store:
                snapshot = store.latest_snapshot()
                kinds: dict[str, int] = {}
                for entity in store.entities():
                    kinds[str(entity.kind)] = kinds.get(str(entity.kind), 0) + 1
                counts = sorted(kinds.items())
        all_pages = guide.pages()
        guide_pages = [page for page in all_pages if page.kind in ("area", "concept")]
        digests = sorted(guide.pages("digest"), key=lambda page: str(page.meta.get("written_at", "")))
        unread_digests: list[Page] = []
        stale: list[Page] = []
        path_progress: list[tuple[Page, int, int]] = []
        if (view.data / "codetrail.db").exists():
            with learning() as state:
                unread = set(state.unread_digests([page.id for page in digests]))
                unread_digests = [page for page in reversed(digests) if page.id in unread]
                statuses = {page.id: state.status(page).state for page in guide_pages}
            stale = [page for page in guide_pages if statuses.get(page.id) == "stale"]
            for path in guide.pages("path"):
                steps = [str(step) for step in path.meta.get("steps") or []]
                learned = sum(1 for step in steps if statuses.get(step) == "learned")
                path_progress.append((path, learned, len(steps)))
        return render(
            "home.html", signal=current_signal(), areas=view.areas(), counts=counts, snapshot=snapshot,
            guide_pages=guide_pages, latest_digest=digests[-1] if digests else None,
            unread_digests=unread_digests, stale_pages=stale, paths=path_progress,
        )  # fmt: skip

    @app.get("/pages/{page_id:path}", response_class=HTMLResponse)
    def guide_page(page_id: str) -> HTMLResponse:
        if not PAGE_ID.fullmatch(page_id) or not (view.data / "codetrail.db").exists():
            return not_found()
        page = guide.read_page(page_id)
        if page is None:
            return not_found()
        manifest = view.manifest()
        changed = manifest is not None and page.kind != "digest" and sources_changed(page, manifest)
        with view.store() as store:
            segments = render_body(page.body, store, settings.diagrams.max_nodes)
        with learning() as state:
            status = state.status(page)
        checks = [{"id": check["id"], "question": check.get("question", ""), "passed": check["id"] in status.passed}
                  for check in page_checks(page)]  # the rubric stays on the server  # fmt: skip
        changes = None
        if status.state == "stale" and status.learned_commit:
            before = guide.file_at(status.learned_commit, page.id)
            if before is not None:
                changes = "\n".join(difflib.unified_diff(
                    parse_page(page.id, before).body.splitlines(), page.body.splitlines(),
                    "when you learned it", "now", lineterm="",
                ))  # fmt: skip
        return render(
            "page.html", page=page, segments=segments, sources_changed=changed, status=status.state, checks=checks,
            changes=changes,
        )  # fmt: skip

    @app.post("/update")
    def start_update() -> Response:
        refusal = job.start()
        if refusal is None:
            return JSONResponse(job.status(), status_code=202)
        busy = job.state in ("preparing", "waiting", "running")
        return JSONResponse({**job.status(), "message": refusal}, status_code=409 if busy else 429)

    @app.get("/update/status")
    def update_status() -> Response:
        status = job.status()
        if "estimate" in status:  # rendered here, in the reader's language and autoescaped, for the page's dialog
            template = environments[language().code].get_template("estimate.html")
            status["estimate_html"] = template.render(estimate=status["estimate"])
        return JSONResponse(status)

    @app.post("/update/confirm")
    def confirm_update(decision: EstimateDecision) -> Response:
        if not job.decide(decision.estimate_id, go_ahead=True):
            return JSONResponse({"error": "That estimate isn't waiting any more; start the update again."}, 428)
        return JSONResponse({"state": "running"})

    @app.post("/update/cancel")
    def cancel_update(decision: EstimateDecision) -> Response:
        if not job.decide(decision.estimate_id, go_ahead=False):
            return JSONResponse({"error": "That estimate isn't waiting any more."}, 428)
        return JSONResponse({"state": "cancelled"})

    @app.get("/areas/{scope:path}", response_class=HTMLResponse)
    def area(scope: str) -> HTMLResponse:
        files_here = view.files_under(scope)
        if not files_here:
            return not_found()
        prefix = scope.rstrip("/") + "/"
        with view.store() as store:
            projects = [
                (project, dependencies_diagram(store, project.id))
                for project in store.entities(EntityKind.PROJECT)
                if project.id.removeprefix("project:").startswith(prefix) or project.id == f"project:{scope}"
            ]
            imports = imports_diagram(store, scope, settings.diagrams.max_nodes)
            decisions = [
                decision
                for decision in store.entities(EntityKind.DECISION)
                if str(decision.attributes.get("path", "")).startswith(prefix)
            ]
        return render(
            "area.html", scope=scope, files=files_here, projects=projects, imports=imports, decisions=decisions
        )

    @app.get("/facts/{fact_id:path}", response_class=HTMLResponse)
    def fact(fact_id: str) -> HTMLResponse:
        if not (view.data / "codetrail.db").exists():
            return not_found()
        with view.store() as store:
            entity = store.entity(fact_id)
            if entity is None:
                return not_found()
            relations = store.relations()
        outgoing = [relation for relation in relations if relation.source_id == fact_id]
        incoming = [relation for relation in relations if relation.target_id == fact_id]
        return render("fact.html", entity=entity, outgoing=outgoing, incoming=incoming)

    @app.get("/source/{path:path}", response_class=HTMLResponse)
    def source(path: str) -> HTMLResponse:
        manifest = view.manifest()
        if manifest is None or path not in manifest.files:
            return not_found()
        text = view.read_source(path)
        lines = text.splitlines() if text is not None else None
        return render("source.html", path=path, commit=manifest.commit, lines=lines)

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions() -> HTMLResponse:
        found = []
        if (view.data / "codetrail.db").exists():
            with view.store() as store:
                found = store.entities(EntityKind.DECISION)
        found.sort(key=lambda decision: str(decision.attributes.get("number", "")))
        return render("decisions.html", decisions=found)

    @app.post("/settings/language")
    def set_language(choice: LanguageChoice) -> Response:
        language = choice.language
        if language not in languages:
            return Response(status_code=400)
        connection = connect(view.data / "codetrail.db")
        try:
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (LANGUAGE_SETTING, language),
            )
        finally:
            connection.close()
        return Response(status_code=204)

    @app.exception_handler(404)
    def missing(request: Request, error: Exception) -> Response:
        # A page with the token goes only to a signed-in reader; /static/ is open, so its 404s stay plain.
        if request.url.path.startswith("/static/") or not session.is_session(request.cookies.get(SESSION_COOKIE)):
            return PlainTextResponse("Not found.", status_code=404)
        return not_found()

    app.include_router(
        bridge_router(
            paths,
            name,
            assistant_for or real_assistant,
            lambda: language().code,
            settings.bridge.max_question_chars,
            settings.diagrams.max_nodes,
            settings.tools.gitleaks,
            settings.prices,
            max(
                settings.providers.claude_code.timeout_seconds,
                settings.providers.codex.timeout_seconds,
                settings.providers.local.timeout_seconds,
            ),
        )
    )
    app.include_router(
        learning_router(
            paths,
            name,
            assistant_for or real_assistant,
            lambda: language().code,
            settings.bridge.max_question_chars,
            settings.learn.grading_cooldown_seconds,
            settings.tools.gitleaks,
            settings.prices,
        )
    )
    app.mount("/static", StaticFiles(directory=str(files("codetrail.web").joinpath("static"))), name="static")
    return SecurityMiddleware(app, session=session, port=settings.server.port)


def _environment(templates: str, language: Language) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(templates),
        autoescape=select_autoescape(["html"], default=True),
        extensions=["jinja2.ext.i18n"],
    )
    environment.install_gettext_translations(language.translations, newstyle=True)  # type: ignore[attr-defined]
    environment.filters["tokens"] = tokens_text
    environment.filters["utc"] = when_text
    environment.filters["dollars"] = lambda value: "" if value is None else f"${value:.2f}"
    return environment


class EstimateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estimate_id: str = Field(max_length=100)


class UpdateJob:
    """One update at a time, run in a background thread; the target's lock also refuses one from the command line.

    The update refreshes the facts (free), then waits with its estimate until the reader confirms it with the
    estimate's id, cancels it, or `estimate_ttl_seconds` pass; only a confirmation lets paid work start (design 15.4).
    """

    def __init__(
        self,
        run: Callable[[Callable[[UpdateEstimate], bool]], object],
        cooldown_seconds: int = 0,
        estimate_ttl_seconds: int = 300,
    ) -> None:
        self._run = run
        self._lock = threading.Lock()
        self._cooldown = cooldown_seconds
        self._ttl = estimate_ttl_seconds
        self._finished_at: float | None = None
        self._decided = threading.Event()
        self._decision = False
        self._declined = False
        self.state = "idle"
        self.message = ""
        self.estimate: dict[str, object] | None = None
        self.estimate_id: str | None = None

    def _confirm(self, estimate: UpdateEstimate) -> bool:
        if not estimate.lines:
            return True
        with self._lock:
            self._decided.clear()
            self._decision = False
            self.estimate, self.estimate_id = estimate.as_json(), secrets.token_urlsafe(16)
            self.state = "waiting"
        self._decided.wait(self._ttl)
        with self._lock:
            # Decided under the lock: a confirmation racing the expiry either counts, or is refused by decide().
            answered = self._decided.is_set()
            self.estimate, self.estimate_id = None, None
            if not answered:
                self.message = "The estimate expired before it was confirmed, so nothing was spent."
            self.state = "running"
            self._declined = not (answered and self._decision)
            return not self._declined

    def decide(self, estimate_id: str, go_ahead: bool) -> bool:
        """Confirms or cancels the waiting estimate; the id works once, and only while it waits."""
        with self._lock:
            expected = self.estimate_id
            if (
                self.state != "waiting"
                or expected is None
                or not hmac.compare_digest(estimate_id.encode(), expected.encode())
            ):
                return False
            self.estimate_id = None
            self._decision = go_ahead
            self._decided.set()
            return True

    def start(self) -> str | None:
        """Starts the update; returns why it can't (one running, or the last one finished too recently)."""
        with self._lock:
            if self.state in ("preparing", "waiting", "running"):
                return "An update is already running."
            if self._finished_at is not None and time.monotonic() - self._finished_at < self._cooldown:
                return "An update finished a moment ago; wait a few minutes before the next."
            self.state, self.message, self._declined = "preparing", "", False
        threading.Thread(target=self._work, daemon=True).start()
        return None

    def _work(self) -> None:
        try:
            result = self._run(self._confirm)
        except CodetrailError as error:
            self.state, self.message = "failed", str(error)
        except Exception as error:  # the page shows a generic message; details stay out of the response
            self.state, self.message = "failed", f"The update failed ({type(error).__name__})."
        else:
            if self._declined or getattr(result, "declined", False):
                reason = self.message or "The update was cancelled."
                self.state, self.message = "declined", f"{reason} The facts were refreshed; the guide wasn't updated."
            else:
                self.state, self.message = "done", ""
        finally:
            self._finished_at = time.monotonic()

    def status(self) -> dict[str, object]:
        if self.state == "waiting" and self.estimate is not None:
            return {"state": self.state, "message": "", "estimate": self.estimate, "estimate_id": self.estimate_id}
        return {"state": self.state, "message": self.message}
