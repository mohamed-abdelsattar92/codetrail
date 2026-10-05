"""The github_actions extractor: deploy evidence from GitHub Actions workflows, and nothing else (design 17.2).

Workflows are read with a SafeLoader that refuses aliases. Each step that deploys (a known command, or a known deploy
action) becomes a `deployment` fact with its kind, its target name when one is given, the folder it runs in and its
line, which comes from the parser. Step text, `env` values and `secrets.*` references are never recorded: only the
kind, a plain target name, a job id in GitHub's grammar and a folder of plain path segments.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

import yaml

from codetrail.extract import FileFacts, Resolution
from codetrail.facts import Entity, EntityKind, Relation, Source

# Only plain names reach a fact, so nothing secret-shaped, templated or instruction-like does.
PLAIN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}")
JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,99}")
SEGMENT = re.compile(r"[A-Za-z0-9._-]+")
COMMANDS = [
    ("cloudflare", re.compile(r"\bwrangler(?:@[\w.^~-]+)?\s+(?:deploy|publish)\b")),
    (
        "google_cloud",
        re.compile(r"\bgcloud\s+run\s+(?:deploy|services\s+replace|jobs\s+deploy)\s+(?P<target>[^\s\\]+)?"),
    ),
    ("terraform", re.compile(r"\bterraform\s+apply\b")),
    ("fly", re.compile(r"\bfly(?:ctl)?\s+deploy\b")),
    ("npm_script", re.compile(r"\b(?:npm\s+run|pnpm\s+run|yarn(?:\s+run)?)\s+deploy\b")),  # `pnpm deploy` copies files
]
ACTIONS = {
    "cloudflare/wrangler-action": "cloudflare",
    "google-github-actions/deploy-cloudrun": "google_cloud",
    "superfly/flyctl-actions": "fly",
}


class _NoAliases(yaml.SafeLoader):
    """safe_load, refusing YAML aliases, so a small file can't expand into an enormous one (a billion-laughs file)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("aliases are not read")
        return super().compose_node(parent, index)


class GitHubActionsExtractor:
    name = "github_actions"
    version = 1

    def __init__(self, max_steps: int = 5000) -> None:
        self._max_steps = max_steps

    def handles(self, path: str) -> bool:
        return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml")) and path.count("/") == 2

    def prepare(self, paths: Sequence[str]) -> None:
        pass

    def extract(self, path: str, content: bytes) -> FileFacts:
        loader = _NoAliases(content.decode("utf-8"))
        try:
            root = loader.get_single_node()
            data = loader.construct_document(root) if root is not None else None
        finally:
            loader.dispose()
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, dict) or root is None:
            return FileFacts(path)
        lines = _step_lines(root)
        entities: list[Entity] = []
        visited = 0
        for job_name, job in jobs.items():
            if not isinstance(job_name, str) or not JOB_ID.fullmatch(job_name):
                continue  # GitHub's job-id grammar; anything else isn't a job, and its text never reaches a fact
            if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
                continue
            defaults = job.get("defaults")
            run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
            job_folder = _folder(run_defaults.get("working-directory")) if isinstance(run_defaults, dict) else ""
            for index, step in enumerate(job["steps"]):
                visited += 1
                if visited > self._max_steps:
                    return FileFacts(path, tuple(entities))
                found = _deploy(step) if isinstance(step, dict) else None
                if found is None:
                    continue
                kind, target = found
                folder = _folder(step.get("working-directory")) if "working-directory" in step else job_folder
                attributes: dict[str, Any] = {"kind": kind, "job": job_name}
                if folder is not None:  # an unusable folder is no folder: no arrow is drawn on a guess
                    attributes["folder"] = folder
                if target and PLAIN_NAME.fullmatch(target):
                    attributes["target"] = target
                line = lines.get((job_name, index))
                entities.append(Entity(f"deployment:{path}#{job_name}/{index}", EntityKind.DEPLOYMENT, attributes,
                                       (Source(path, line, line) if line else Source(path),)))  # fmt: skip
        return FileFacts(path, tuple(entities))

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Any]) -> Resolution:
        return Resolution(list[Relation]())


def _step_lines(root: yaml.Node) -> dict[tuple[str, int], int]:
    """Each step's line, from the parser's own positions: exact, and one pass over the document."""
    lines: dict[tuple[str, int], int] = {}
    jobs = _value(root, "jobs")
    if not isinstance(jobs, yaml.MappingNode):
        return lines
    for key, job in jobs.value:
        steps = _value(job, "steps")
        if isinstance(key, yaml.ScalarNode) and isinstance(steps, yaml.SequenceNode):
            for index, step in enumerate(steps.value):
                lines[(str(key.value), index)] = step.start_mark.line + 1
    return lines


def _value(node: yaml.Node, name: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    return next((value for key, value in node.value if isinstance(key, yaml.ScalarNode) and key.value == name), None)


def _folder(value: Any) -> str | None:
    """A working directory as a repository folder ("" is the root), or None unless every part is a plain segment."""
    if value is None:
        return ""
    if not isinstance(value, str) or value.startswith("/"):
        return None
    parts = [part for part in PurePosixPath(value).parts if part != "."]
    if any(part == ".." or not SEGMENT.fullmatch(part) for part in parts):
        return None  # templated, escaping, or not a plain folder name
    return "/".join(parts)


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
