"""The github_actions extractor: deploy evidence from GitHub Actions workflows, and nothing else (design 17.2).

Workflows are read with `yaml.safe_load`. Each step that deploys (a known command, or a known deploy action) becomes a
`deployment` fact with its kind, its target name when one is given, the folder it runs in and its line. Step text,
`env` values and `secrets.*` references are never recorded: only the kind, a plain target name and a folder.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

import yaml

from codetrail.extract import FileFacts, Resolution
from codetrail.facts import Entity, EntityKind, Relation, Source

# A target name is kept only when it's a plain name, so nothing secret-shaped or templated reaches a fact.
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}")
COMMANDS = [
    ("cloudflare", re.compile(r"\bwrangler(?:@[\w.^~-]+)?\s+(?:deploy|publish)\b")),
    (
        "google_cloud",
        re.compile(r"\bgcloud\s+run\s+(?:deploy|services\s+replace|jobs\s+deploy)\s+(?P<target>[^\s\\]+)?"),
    ),
    ("terraform", re.compile(r"\bterraform\s+apply\b")),
    ("fly", re.compile(r"\bfly(?:ctl)?\s+deploy\b")),
    ("npm_script", re.compile(r"\b(?:npm\s+run|pnpm(?:\s+run)?|yarn(?:\s+run)?)\s+deploy\b")),
]
MAX_STEPS = 5000  # per workflow file: more is not a real workflow


class _NoAliases(yaml.SafeLoader):
    """safe_load, refusing YAML aliases, so a small file can't expand into an enormous one (a billion-laughs file)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("aliases are not read")
        return super().compose_node(parent, index)


ACTIONS = {
    "cloudflare/wrangler-action": "cloudflare",
    "google-github-actions/deploy-cloudrun": "google_cloud",
    "superfly/flyctl-actions": "fly",
}


class GitHubActionsExtractor:
    name = "github_actions"
    version = 1

    def handles(self, path: str) -> bool:
        return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")) and path.count("/") == 2

    def prepare(self, paths: Sequence[str]) -> None:
        pass

    def extract(self, path: str, content: bytes) -> FileFacts:
        text = content.decode("utf-8")
        data = yaml.load(text, Loader=_NoAliases)  # noqa: S506 - a SafeLoader that also refuses aliases
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, dict):
            return FileFacts(path)
        lines = text.splitlines()
        entities: list[Entity] = []
        visited = 0
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
                continue
            defaults = job.get("defaults")
            run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
            job_folder = _folder(run_defaults.get("working-directory")) if isinstance(run_defaults, dict) else ""
            for index, step in enumerate(job["steps"]):
                visited += 1
                if visited > MAX_STEPS:
                    return FileFacts(path, tuple(entities))
                if not isinstance(step, dict):
                    continue
                found = _deploy(step)
                if found is None:
                    continue
                kind, target = found
                folder = _folder(step.get("working-directory")) if "working-directory" in step else job_folder
                attributes: dict[str, Any] = {"kind": kind, "job": str(job_name)}
                if folder is not None:  # an unusable folder is no folder: no arrow is drawn on a guess
                    attributes["folder"] = folder
                if target and PLAIN_NAME.fullmatch(target):
                    attributes["target"] = target
                line = _line_of(lines, step)
                entities.append(Entity(f"deployment:{path}#{job_name}/{index}", EntityKind.DEPLOYMENT, attributes,
                                       (Source(path, line, line) if line else Source(path),)))  # fmt: skip
        return FileFacts(path, tuple(entities))

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Any]) -> Resolution:
        return Resolution(list[Relation]())


def _folder(value: Any) -> str | None:
    """A working directory as a repository folder ("" is the root); None when it's templated, absolute or escaping."""
    if value is None:
        return ""
    if not isinstance(value, str) or "$" in value or value.startswith("/"):
        return None
    parts = [part for part in PurePosixPath(value).parts if part != "."]
    return None if ".." in parts else "/".join(parts)


def _deploy(step: Mapping[str, Any]) -> tuple[str, str | None] | None:
    uses = step.get("uses")
    if isinstance(uses, str):
        action = uses.split("@", 1)[0]
        if action in ACTIONS:
            settings = step.get("with") if isinstance(step.get("with"), dict) else {}
            target = settings.get("service") if isinstance(settings, dict) else None
            return ACTIONS[action], target if isinstance(target, str) else None
    run = step.get("run")
    if isinstance(run, str):
        for kind, pattern in COMMANDS:
            match = pattern.search(run)
            if match:
                return kind, match.groupdict().get("target")
    return None


def _line_of(lines: list[str], step: Mapping[str, Any]) -> int | None:
    """The line where the step's `uses` or `run` key starts, found by its first words (never stored)."""
    for key in ("uses", "run", "name"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            first = value.strip().splitlines()[0][:40]
            for number, line in enumerate(lines, start=1):
                if first in line:
                    return number
    return None
