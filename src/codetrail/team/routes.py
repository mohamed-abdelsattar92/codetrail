"""The add-repositories screen (design section 24.6): each service's sign-in, a source's repositories, and saving the
team's selection, which writes the team and its new targets, mounts them and starts their free first refresh.

The page itself calls no service: its script asks the JSON reads below, which need the session's token as well as
the session (design 16.3). The sync routes run in FastAPI's thread pool, one worker per request, and each program
run is bounded by `hosting.timeout_seconds`. Every name, id and branch a selection names is checked against what
the service lists again here, before anything is written.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment
from markupsafe import Markup
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from codetrail.config import GlobalConfig, Paths, TeamSource, load_team, validate_branch_name
from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository, HostingService
from codetrail.hosting.names import AZURE_ORGANIZATION, SERVICE_NAMES, Service, require_safe_id, validate_source
from codetrail.team.refresh import TeamRefreshJob
from codetrail.team.settings import save_selection, team_targets
from codetrail.team.sync import TeamSync, sync_team
from codetrail.web.i18n import Language
from codetrail.web.repository_view import plural_templates, script_json
from codetrail.web.security import SessionState

logger = logging.getLogger(__name__)
MAX_IDS = 10_000  # the most `hosting.max_repositories_per_source` allows; the setting itself is checked per request
Text = Annotated[str, StringConstraints(max_length=300)]


class SourceSelection(BaseModel):
    """What the screen posts: one source's selection, as the team's settings keep it, and a branch for each new
    repository that isn't taught on its default branch."""

    model_config = ConfigDict(extra="forbid")
    service: Service
    source: Text
    follow: Literal["all", "chosen"]
    clone_over: Literal["https", "ssh"]
    left_out: list[Text] = Field(default=[], max_length=MAX_IDS)
    chosen: list[Text] = Field(default=[], max_length=MAX_IDS)
    branches: dict[Text, Text] = Field(default={}, max_length=MAX_IDS)


class Refused(Exception):
    """A request the screen shouldn't have sent, or a service that couldn't answer; nothing was written."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def team_router(
    paths: Paths,
    name: str,
    settings: GlobalConfig,
    services: Mapping[Service, HostingService],
    session: SessionState,
    language: Language,
    environment: Environment,
    mount: Callable[[str], None],
    refresh: TeamRefreshJob,
    record_sync: Callable[[TeamSync], None],
) -> APIRouter:
    """`mount(target)` holds and serves a new target's page; `record_sync` gives the team page the latest listing."""
    router = APIRouter()
    _ = language.translations.gettext
    saving = threading.Lock()

    def service_for(service: Service) -> HostingService:
        if service not in services:
            raise Refused(_("That service isn't available."))
        return services[service]

    def checked_source(service: Service, source: str) -> str:
        try:
            return validate_source(service, source)
        except CodetrailError as error:
            raise Refused(str(error)) from error

    def safe_id(identifier: str) -> str:
        try:
            return require_safe_id(identifier)
        except CodetrailError as error:
            raise Refused(str(error)) from error

    def listed(service: Service, source: str) -> list[HostedRepository]:
        try:
            return service_for(service).repositories(checked_source(service, source))
        except CodetrailError as error:  # the program's own message, already Codetrail's (design 24.3)
            raise Refused(str(error), 502) from error

    def answer(read: Callable[[], dict[str, object]]) -> Response:
        try:
            return JSONResponse(read())
        except Refused as refusal:
            return JSONResponse({"error": str(refusal)}, refusal.status)

    def team_repositories(service: Service, source: str) -> dict[str, tuple[str, str]]:
        """The team's targets in that source, by the service's id: each one's repository name and branch."""
        return {
            config.remote.id: (config.remote.name, config.branch)
            for config in team_targets(paths, name).values()
            if config.remote is not None and (config.remote.service, config.remote.source) == (service, source)
        }

    @router.get("/team/add", response_class=HTMLResponse)
    def add_screen() -> HTMLResponse:
        html = environment.get_template("team_add.html").render(
            language=language, team=name, token=session.token, active_days=settings.team.active_days,
            labels=Markup(script_json(labels(language))),  # noqa: S704 - escaped for a JSON script block
        )  # fmt: skip
        return HTMLResponse(html)

    @router.get("/team/hosting/sources")
    def hosting_sources(organization: Annotated[str | None, Query(max_length=100)] = None) -> Response:
        def read() -> dict[str, object]:
            if organization is not None and not AZURE_ORGANIZATION.fullmatch(organization):
                raise Refused(_("That isn't an Azure DevOps organization's name."))
            statuses: list[dict[str, object]] = []
            sources: list[dict[str, object]] = []
            for service, adapter in services.items():
                status = adapter.status()
                statuses.append(asdict(status))
                if not status.signed_in:
                    continue
                try:  # Azure DevOps lists the named organization's projects; the others ignore it
                    sources.extend(asdict(found) for found in adapter.sources(organization))
                except CodetrailError as error:
                    logger.warning("Couldn't list the %s sources: %s", service, error)
            return {"services": statuses, "sources": sources}

        return answer(read)

    @router.get("/team/hosting/repositories")
    def hosting_repositories(service: Service, source: Annotated[str, Query(max_length=300)]) -> Response:
        def read() -> dict[str, object]:
            repositories = listed(service, source)
            targets = team_repositories(service, source)
            earliest = datetime.now(UTC) - timedelta(days=settings.team.active_days)
            rows = [
                row(repository, earliest, targets.get(repository.id))
                for repository in repositories
                if not repository.disabled and repository.default_branch is not None  # never followed (design 24.5)
            ]
            # A followed repository the service no longer lists is shown, so the reader can leave it out.
            shown = {repository.id for repository in repositories}
            rows.extend(
                gone_row(identifier, *target) for identifier, target in targets.items() if identifier not in shown
            )
            selection = next((entry for entry in load_team(paths, name).sources
                              if (entry.service, entry.source) == (service, source)), None)  # fmt: skip
            following = None if selection is None else {
                "follow": selection.follow, "left_out": selection.left_out, "chosen": selection.chosen,
                "clone_over": selection.clone_over,
            }  # fmt: skip
            return {"repositories": rows, "following": following}

        return answer(read)

    @router.get("/team/hosting/branches")
    def hosting_branches(
        service: Service,
        source: Annotated[str, Query(max_length=300)],
        repository_id: Annotated[str, Query(alias="id", max_length=300)],
    ) -> Response:
        def read() -> dict[str, object]:
            safe_id(repository_id)
            repository = next((found for found in listed(service, source) if found.id == repository_id), None)
            if repository is None:
                raise Refused(_("That repository isn't in the source."))
            try:
                return {"branches": service_for(service).branches(repository)}
            except CodetrailError as error:
                raise Refused(str(error), 502) from error

        return answer(read)

    def checked_branch(branch: str) -> str:
        if branch.startswith("-"):
            raise Refused(_("A branch name can't start with “-”."))
        try:
            validate_branch_name(branch)
        except CodetrailError as error:
            raise Refused(str(error)) from error
        return branch

    def checked(selection: SourceSelection) -> tuple[TeamSource, dict[str, str]]:
        """The selection as the team's settings keep it, and the branches, or `Refused`: first what needs no service,
        then every id and branch against what the service lists now."""
        limit = settings.hosting.max_repositories_per_source
        if max(len(selection.left_out), len(selection.chosen), len(selection.branches)) > limit:
            raise Refused(_("A selection can name at most %(count)s repositories.") % {"count": limit})
        source = checked_source(selection.service, selection.source)
        for identifier in [*selection.left_out, *selection.chosen, *selection.branches]:
            safe_id(identifier)
        for branch in selection.branches.values():
            checked_branch(branch)
        repositories = {repository.id: repository for repository in listed(selection.service, source)}
        targets = team_repositories(selection.service, source)
        # A repository the team follows may be left out after the service stopped listing it, so it can be removed.
        unknown = ({*selection.chosen, *selection.branches} - set(repositories)) | (
            set(selection.left_out) - set(repositories) - set(targets)
        )
        if unknown:
            raise Refused(_("Some of those repositories aren't in the source."))
        branches: dict[str, str] = {}
        for identifier, branch in selection.branches.items():
            repository = repositories[identifier]
            if branch != repository.default_branch:
                try:
                    found = service_for(selection.service).branches(repository)
                except CodetrailError as error:
                    raise Refused(str(error), 502) from error
                if branch not in found:
                    raise Refused(_("A chosen branch isn't in its repository."))
            branches[identifier] = branch
        team_source = TeamSource(
            service=selection.service, source=source, follow=selection.follow, clone_over=selection.clone_over,
            left_out=selection.left_out, chosen=selection.chosen,
        )  # fmt: skip
        return team_source, branches

    @router.post("/team/repositories")
    def save_repositories(selection: SourceSelection) -> Response:
        # One save at a time, and none while the last one's repositories are still being refreshed.
        if refresh.running or not saving.acquire(blocking=False):
            return JSONResponse({"error": _("The repositories added last are still being refreshed.")}, 409)
        try:
            team_source, branches = checked(selection)
            save_selection(paths, name, team_source)
            result = sync_team(paths, name, services, datetime.now(UTC), branches=branches)
            record_sync(result)
            for target in result.added:
                mount(target)
            started = bool(result.added) and refresh.start(result.added)
        except Refused as refusal:
            return JSONResponse({"error": str(refusal)}, refusal.status)
        except CodetrailError as error:  # the team is being removed, say
            return JSONResponse({"error": str(error)}, 409)
        finally:
            saving.release()
        return JSONResponse({"added": result.added, "refresh": started})

    @router.get("/team/refresh/status")
    def refresh_status(after: Annotated[int, Query(ge=0, le=2**53)] = 0) -> Response:
        return JSONResponse(refresh.status(after))

    return router


def row(repository: HostedRepository, earliest: datetime, target: tuple[str, str] | None) -> dict[str, object]:
    """A listed repository as the screen shows it, with its target's branch when the team follows it already. One
    whose service reports no push time counts as active."""
    pushed_at = repository.pushed_at
    return {
        "id": repository.id,
        "name": repository.name,
        "default_branch": repository.default_branch,
        "branch": repository.default_branch if target is None else target[1],
        "archived": repository.archived,
        "pushed_at": None if pushed_at is None else pushed_at.isoformat(),
        "size_kb": repository.size_kb,
        "active": pushed_at is None or pushed_at >= earliest,
        "followed": target is not None,
        "gone": False,
    }


def gone_row(identifier: str, name: str, branch: str) -> dict[str, object]:
    """A followed repository the service no longer lists, from its target's settings."""
    return {
        "id": identifier,
        "name": name,
        "default_branch": branch,
        "branch": branch,
        "archived": False,
        "pushed_at": None,
        "size_kb": None,
        "active": False,
        "followed": True,
        "gone": True,
    }


def labels(language: Language) -> dict[str, object]:
    """The screen script's text, from the reader's catalog: `{name}` marks a value the script fills in."""
    t = language.translations
    return {
        "services": SERVICE_NAMES,
        "signedInAs": t.gettext("Signed in as %(account)s") % {"account": "{account}"},
        "signedIn": t.gettext("Signed in"),
        "signedOut": t.gettext("Signed out. To sign in, run:"),
        "notInstalled": t.gettext("Not installed."),
        "chooseSource": t.gettext("Choose a source"),
        "noSources": t.gettext("No source to show yet: sign in to a service, or name an Azure DevOps organization."),
        "listing": t.gettext("Listing…"),
        "couldNotList": t.gettext("Couldn't list them just now."),
        "followAll": t.gettext("Follow the whole source"),
        "leftOut": plural_templates(
            lambda n: t.ngettext(
                "Follow the whole source (%(count)s repository left out)",
                "Follow the whole source (%(count)s repositories left out)",
                n,
            )
        ),
        "add": plural_templates(lambda n: t.ngettext("Add %(count)s repository", "Add %(count)s repositories", n)),
        "pushedToday": t.gettext("Pushed today"),
        "pushedDaysAgo": plural_templates(
            lambda n: t.ngettext("Pushed %(count)s day ago", "Pushed %(count)s days ago", n)
        ),
        "sizeKb": t.gettext("%(size)s KB") % {"size": "{size}"},
        "sizeMb": t.gettext("%(size)s MB") % {"size": "{size}"},
        "archived": t.gettext("Archived"),
        "whenActive": t.gettext("Followed once it's active"),
        "gone": t.gettext("No longer in the source"),
        "branchOf": t.gettext("Branch of %(name)s") % {"name": "{name}"},
        "noMatch": t.gettext("No repository matches."),
        "saving": t.gettext("Saving…"),
        "saveFailed": t.gettext("Couldn't save the selection."),
        "nothingNew": t.gettext("Saved. There's no new repository to get ready."),
        "running": t.gettext("Getting the new repositories ready (free)…"),
        "finished": t.gettext("Done. Their first guides wait for the next catch-up's estimate."),
        "started": t.gettext("Cloning %(target)s and reading its facts…") % {"target": "{target}"},
        "done": t.gettext("%(target)s is ready.") % {"target": "{target}"},
        "failed": t.gettext("%(target)s couldn't be refreshed: %(reason)s")
        % {"target": "{target}", "reason": "{reason}"},
    }
