"""The local page's application: routes over one target's facts, sources and signal (design section 7.1).

Every route is behind the security middleware. Templates autoescape; interface text comes from the reader's catalog,
and guide content is marked as English.
"""

from __future__ import annotations

import time
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp

from codetrail.config import GlobalConfig, Paths
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.facts import EntityKind
from codetrail.repo.signal import Signal, behind
from codetrail.web.diagrams import dependencies_diagram, imports_diagram
from codetrail.web.i18n import Language, installed_languages
from codetrail.web.security import SESSION_COOKIE, SecurityMiddleware, SessionState, login_response
from codetrail.web.target_view import TargetView

LANGUAGE_SETTING = "language"


class LanguageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(max_length=16)


def create_app(
    paths: Paths, name: str, session: SessionState, settings: GlobalConfig, locales: Path | None = None
) -> ASGIApp:
    """The page, wrapped in the security middleware outside everything, so every response passes through it."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    view = TargetView(paths, name)
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

    def render(template: str, status_code: int = 200, **context: Any) -> HTMLResponse:
        current = language()
        html = (
            environments[current.code]
            .get_template(template)
            .render(language=current, languages=list(languages.values()), token=session.token, target=name, **context)
        )
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
        return render("home.html", signal=current_signal(), areas=view.areas(), counts=counts, snapshot=snapshot)

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

    app.mount("/static", StaticFiles(directory=str(files("codetrail.web").joinpath("static"))), name="static")
    return SecurityMiddleware(app, session=session, port=settings.server.port)


def _environment(templates: str, language: Language) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(templates),
        autoescape=select_autoescape(["html"], default=True),
        extensions=["jinja2.ext.i18n"],
    )
    environment.install_gettext_translations(language.translations, newstyle=True)  # type: ignore[attr-defined]
    return environment
