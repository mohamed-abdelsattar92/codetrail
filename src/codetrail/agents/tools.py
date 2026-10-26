"""The four tools coding agents call: the map, a brief, a page and the changes (design sections 23.5 and 23.7).

They read only the target's exported `index.json`: never `source/`, the mirror, the guide or the database. They run
no git, call no assistant and never take the update lock, so a reader's `codetrail update` runs while agents call
them; each call holds the target's shared in-use lock for its own length, as the page's requests do. Every argument
is checked before it is used, and a path is only matched against the pages' scopes, never opened.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from datetime import date

from codetrail.agents.convert import limit_words
from codetrail.agents.export import AgentExport, ExportedPage, read_export
from codetrail.config import AgentSettings, Paths
from codetrail.errors import CodetrailError
from codetrail.generate.outline import in_scope
from codetrail.guide import PAGE_ID
from codetrail.lock import target_in_use
from codetrail.repo.source import safe_path

COMMIT = re.compile(r"[0-9a-f]{7,40}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_PATH_CHARS = 1000


class ToolInputError(CodetrailError):
    """A bad argument: the agent is told what's wrong, as a tool error."""


def _schema(properties: dict[str, object], required: list[str]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


PAGE = {"type": "string", "description": "A guide page id, such as areas/api or concepts/contract-first"}
TOOLS: list[dict[str, object]] = [
    {
        "name": "codetrail_map",
        "description": "Codetrail's map of the repository: its parts, decisions, recent changes and guide pages. Call "
        "it before starting a task.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "codetrail_brief",
        "description": "The brief for coding agents of a guide page, or of the pages covering a path: key files, "
        "conventions and the reasons for them. Give a page or a path, not both.",
        "inputSchema": _schema(
            {"page": PAGE, "path": {"type": "string", "description": "A path relative to the repository's root"}}, []
        ),
    },
    {
        "name": "codetrail_page",
        "description": "A whole guide page, for the full explanation behind a brief.",
        "inputSchema": _schema({"page": PAGE}, ["page"]),
    },
    {
        "name": "codetrail_changes",
        "description": "The guide's digests of what changed since a commit or a date, newest first.",
        "inputSchema": _schema(
            {
                "since": {
                    "type": "string",
                    "description": "A commit (7 to 40 lower-case hex characters) or a date (YYYY-MM-DD)",
                }
            },
            ["since"],
        ),
    },
]
ARGUMENTS = {tool["name"]: set(tool["inputSchema"]["properties"]) for tool in TOOLS}  # type: ignore[index]


class AgentTools:
    def __init__(self, paths: Paths, name: str, settings: AgentSettings, repository: str) -> None:
        self.paths = paths
        self.name = name
        self.settings = settings
        self.repository = repository

    def definitions(self) -> list[dict[str, object]]:
        """The tools, each saying which repository it covers: the server is registered for the reader, so agents
        working in other repositories see it too."""
        scope = f"For {self.name}, the repository at {self.repository}, only; in any other repository don't call it. "
        return [{**tool, "description": scope + str(tool["description"])} for tool in TOOLS]

    def call(self, tool: str, arguments: Mapping[str, object]) -> str:
        """The tool's answer; raises ToolInputError for a bad argument and KeyError for an unknown tool."""
        handlers: dict[str, Callable[[AgentExport, Mapping[str, object]], str]] = {
            "codetrail_map": lambda export, _arguments: export.map_text,
            "codetrail_brief": self._brief,
            "codetrail_page": lambda export, given: _page(export, given["page"]).text,
            "codetrail_changes": self._changes,
        }
        handler = handlers[tool]
        unknown = set(arguments) - ARGUMENTS[tool]
        if unknown:
            raise ToolInputError(f"{tool} takes no {', '.join(sorted(unknown))}.")
        with ExitStack() as stack:
            try:
                stack.enter_context(target_in_use(self.paths, self.name))
            except CodetrailError:
                return f"Codetrail no longer has the target {self.name!r}."
            export = read_export(self.paths.target_data(self.name) / "agents")
            if export is None:
                return (f"Codetrail has no context for {self.name} yet: run codetrail update {self.name} or "
                        f"codetrail agents {self.name}.")  # fmt: skip
            answer = handler(export, arguments)
        limit = self.settings.max_response_words
        text, left_out = limit_words(answer.rstrip(), limit)
        if left_out:
            text += f"\n\n(cut at {limit} words; ask for one page with codetrail_page or codetrail_brief.)"
        return f"{text}\n\nDescribes {export.commit[:12]}.\n"

    def _brief(self, export: AgentExport, arguments: Mapping[str, object]) -> str:
        if ("page" in arguments) == ("path" in arguments):
            raise ToolInputError("Give codetrail_brief either a page or a path, not both.")
        if "page" in arguments:
            page = _page(export, arguments["page"])
            if page.kind == "digest":
                raise ToolInputError(f"{page.id} is a digest; digests have no brief.")
            return page.brief
        path = _path(arguments["path"])
        best: tuple[int, ExportedPage] | None = None
        for page in export.pages:
            if page.kind == "area":
                for scope in page.scope_paths:
                    if in_scope(path, [scope]) and (best is None or len(scope.strip("/")) > best[0]):
                        best = (len(scope.strip("/")), page)
        concepts = [page for page in export.pages if page.kind == "concept" and in_scope(path, page.scope_paths)]
        chosen = ([best[1]] if best else []) + concepts
        if not chosen:
            return f"No page covers {path}; the map's Pages list what the guide covers."
        return "\n\n".join(page.brief.rstrip() for page in chosen[: self.settings.max_briefs_per_call])

    def _changes(self, export: AgentExport, arguments: Mapping[str, object]) -> str:
        since = arguments.get("since")
        digests = [page for page in export.pages if page.kind == "digest"]
        notice = ""
        if isinstance(since, str) and DATE.fullmatch(since):
            try:
                date.fromisoformat(since)
            except ValueError as error:
                raise ToolInputError(f"{since} isn't a date.") from error
            found = [page for page in digests if page.written_at[:10] >= since]
        elif isinstance(since, str) and COMMIT.fullmatch(since):
            matches = {commit for commit in export.snapshots if commit.startswith(since)}
            if len(matches) > 1:
                raise ToolInputError(f"{since} matches several commits; give more characters.")
            if matches:
                positions = {commit: index for index, commit in enumerate(export.snapshots)}  # each commit's last
                after = positions[matches.pop()]
                found = [page for page in digests if positions.get(page.built_at, -1) > after]
            else:
                notice = f"{since} isn't a commit this guide knows; here are the latest digests.\n\n"
                found = digests[: self.settings.recent_digests]
        else:
            raise ToolInputError("Give a commit (7 to 40 lower-case hexadecimal characters) or a date (YYYY-MM-DD).")
        return notice + ("\n\n".join(page.text.rstrip() for page in found) or "No digests since then.")


def _page(export: AgentExport, value: object) -> ExportedPage:
    if not isinstance(value, str) or not PAGE_ID.fullmatch(value):
        raise ToolInputError("A page id looks like areas/<name> or concepts/<name>.")
    for page in export.pages:
        if page.id == value:
            return page
    raise ToolInputError(f"{value} isn't a page in this guide; the map's Pages list them.")


def _path(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_PATH_CHARS:
        raise ToolInputError(f"A path is text of at most {MAX_PATH_CHARS} characters.")
    if any(unicodedata.category(character) in ("Cc", "Cf") for character in value):
        raise ToolInputError("A path can't hold control characters.")
    path = value.strip()
    while path.startswith("./"):
        path = path[2:]
    path = path.rstrip("/")
    if not safe_path(path):
        raise ToolInputError(f"{path or value!r} isn't a path relative to the repository's root.")
    return path
