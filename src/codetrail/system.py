"""The system pass: a repository's parts and the connections between them, from facts (design section 17.3).

It runs after the extractors in every update. It is plain code over the facts and a few allowed files, read through a
reader that refuses anything outside the allowed-files list; it records each connection with its evidence (explicit,
with the file and line, or matched, with the rule and both names) and never connects parts any other way. It can't
fail an update: a rule that meets something unexpected adds a warning and moves on.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

SERVER_FRAMEWORKS = {
    "package:pypi/fastapi",
    "package:pypi/starlette",
    "package:pypi/flask",
    "package:pypi/django",
    "package:pypi/litestar",
    "package:pypi/sanic",
    "package:pypi/aiohttp",
    "package:pypi/quart",
    "package:npm/express",
    "package:npm/fastify",
    "package:npm/hono",
    "package:npm/koa",
    "package:npm/@nestjs/core",
    "package:swift/vapor",
}
SITE_FRAMEWORKS = {
    "package:npm/astro",
    "package:npm/next",
    "package:npm/vite",
    "package:npm/@sveltejs/kit",
    "package:npm/nuxt",
    "package:npm/gatsby",
    "package:npm/@remix-run/react",
    "package:npm/react-scripts",
}
PLATFORMS = {
    "google_cloud": "Google Cloud",
    "aws": "AWS",
    "azure": "Azure",
    "cloudflare": "Cloudflare",
    "fly": "Fly.io",
}
RESOURCE_PLATFORMS = {"google_": "google_cloud", "aws_": "aws", "azurerm_": "azure", "cloudflare_": "cloudflare"}
# Runtime services whose names are matched against part folders (a dashed arrow, never a solid one).
NAMED_SERVICES = {
    "google_cloud_run_v2_service",
    "google_cloud_run_service",
    "google_cloud_run_v2_job",
    "google_cloudfunctions2_function",
    "cloudflare_workers_script",
    "aws_lambda_function",
    "aws_ecs_service",
    "azurerm_container_app",
}
CONFIG_NAMES = {"package.json", "Package.swift", "Makefile", "justfile", "pyproject.toml"}
# Matched once, after the last "generator" in a name, so a hostile name costs linear time.
GENERATOR_TAIL = re.compile(r"[-_.a-z]*\.ya?ml|[-_.a-z]*")
WORD = re.compile(r"[a-z0-9]+")
TOKEN_SPLIT = re.compile(r"[\s\"'`=,;:()<>\[\]{}]+")


@dataclass(frozen=True)
class SystemFacts:
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)


@dataclass
class Part:
    id: str
    kind: str  # service, app, library, contract, infrastructure, platform
    name: str
    folder: str  # "" for the root; a contract's folder is its document's
    source: Source
    file: str = ""  # a contract's document


def _location(fact_id: str) -> str:
    location = fact_id.split(":", 1)[1]
    return "" if location == "." else location


def _normal(folder: str, relative: str) -> str | None:
    """`folder/relative` lexically normalized; None when it is absolute, templated or climbs out of the repository."""
    if relative.startswith("/") or "$" in relative:
        return None
    parts = [part for part in folder.split("/") if part]
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


class _System:
    def __init__(
        self,
        entities: Iterable[Entity],
        relations: Iterable[Relation],
        files: Mapping[str, str],
        read: Callable[[str], bytes | None],
    ) -> None:
        self.entities = {entity.id: entity for entity in entities}
        self.relations = list(relations)
        self.files = files
        self.read = read
        self.parts: dict[str, Part] = {}
        self.out: list[Relation] = []
        self.seen: set[tuple[str, str, str]] = set()
        self.warnings: list[str] = []
        self.counts: Counter[str] = Counter()
        self.owners: dict[str, Part | None] = {}  # folder -> its owner, once code and infrastructure parts are known

    # Parts --------------------------------------------------------------------------------------------------------
    def add_part(self, part: Part) -> None:
        self.parts.setdefault(part.id, part)

    def platform(self, key: str) -> str:
        part_id = f"part:platform:{key}"
        self.add_part(Part(part_id, "platform", PLATFORMS[key], "", Source("")))
        return part_id

    def find_parts(self) -> None:
        depends: dict[str, set[str]] = {}
        for relation in self.relations:
            if relation.kind is RelationKind.DEPENDS_ON:
                depends.setdefault(relation.source_id, set()).add(relation.target_id)
        for entity in self.entities.values():
            if entity.kind is EntityKind.PROJECT:
                folder = _location(entity.id)
                needs = depends.get(entity.id, set())
                kind = "service" if needs & SERVER_FRAMEWORKS else "app" if needs & SITE_FRAMEWORKS else "library"
                name = str(entity.attributes.get("name") or PurePosixPath(folder).name or "root")
                self.add_part(
                    Part(
                        f"part:{folder or '.'}", kind, name, folder, entity.sources[0] if entity.sources else Source("")
                    )
                )
            elif entity.kind is EntityKind.TERRAFORM_MODULE:
                folder = _location(entity.id)
                self.add_part(
                    Part(
                        f"part:{folder or '.'}",
                        "infrastructure",
                        folder,
                        folder,
                        entity.sources[0] if entity.sources else Source(""),
                    )
                )
        for path in self.files:
            if path.endswith(".xcodeproj/project.pbxproj"):
                folder = str(PurePosixPath(path).parent.parent)
                folder = "" if folder == "." else folder
                name = PurePosixPath(path).parent.stem
                self.parts[f"part:{folder or '.'}"] = Part(f"part:{folder or '.'}", "app", name, folder, Source(path))
        for entity in self.entities.values():
            if entity.kind is EntityKind.WORKER:
                folder = _location(entity.id)
                if f"part:{folder or '.'}" not in self.parts:
                    name = str(entity.attributes.get("name") or PurePosixPath(folder).name)
                    self.add_part(Part(f"part:{folder or '.'}", "service", name, folder, entity.sources[0]))
        documents = sorted(
            {
                source.path
                for entity in self.entities.values()
                if entity.kind in (EntityKind.ROUTE, EntityKind.SCHEMA)
                and not str(entity.attributes.get("project", ""))
                for source in entity.sources
            }
        )
        by_blob: dict[str, list[str]] = {}
        for document in documents:
            by_blob.setdefault(self.files.get(document, document), []).append(document)
        for copies in by_blob.values():
            # Identical documents are one contract: the original is the one no code part holds, else the shortest path;
            # the copies are evidence of who uses it (the contracts rule reads them by blob).
            document = min(copies, key=lambda path: (self.owner(path) is not None, len(path), path))
            folder = str(PurePosixPath(document).parent)
            self.add_part(
                Part(
                    f"part:{document}",
                    "contract",
                    PurePosixPath(document).name,
                    "" if folder == "." else folder,
                    Source(document),
                    file=document,
                )
            )

    def owner(self, path: str) -> Part | None:
        """The innermost code or infrastructure part whose folder holds `path`."""
        folder = path
        visited: list[str] = []
        while True:  # one lookup per folder level, remembered, so neither size nor depth makes this slow
            if folder in self.owners:
                found = self.owners[folder]
                break
            visited.append(folder)
            part = self.parts.get(f"part:{folder or '.'}")
            if part is not None and part.kind not in ("contract", "platform") and part.folder == folder:
                found = part
                break
            if not folder:
                found = None
                break
            folder = folder.rpartition("/")[0]
        for each in visited:
            self.owners[each] = found
        return found

    # Connections --------------------------------------------------------------------------------------------------
    def connect(
        self,
        source: str,
        kind: RelationKind,
        target: str,
        rule: str,
        attributes: dict[str, Any],
        sources: tuple[Source, ...] = (),
    ) -> None:
        if source == target or (source, str(kind), target) in self.seen:
            return
        self.seen.add((source, str(kind), target))
        self.out.append(Relation(source, kind, target, attributes, sources))
        self.counts[rule] += 1

    def depends(self) -> None:
        for relation in self.relations:
            if relation.kind not in (RelationKind.DEPENDS_ON, RelationKind.IMPORTS):
                continue
            source, target = self.entities.get(relation.source_id), self.entities.get(relation.target_id)
            if source is None or target is None or target.kind is EntityKind.PACKAGE:
                continue
            from_part, to_part = self.part_of(source), self.part_of(target)
            if from_part and to_part and from_part.id != to_part.id:
                evidence = relation.sources[0] if relation.sources else Source("")
                self.connect(
                    from_part.id,
                    RelationKind.DEPENDS_ON,
                    to_part.id,
                    "depends on",
                    {"evidence": "explicit", "source": _cite(evidence)},
                    relation.sources[:1],
                )
        for path in self.files:
            if not path.endswith(".xcodeproj/project.pbxproj"):
                continue
            app = self.parts.get(f"part:{_folder(str(PurePosixPath(path).parent.parent)) or '.'}")
            content = self.read(path)
            if app is None or content is None:
                continue
            for number, line in enumerate(content.decode("utf-8", "replace").splitlines(), start=1):
                relative = _pbxproj_package(line)
                package_folder = _normal(app.folder, relative) if relative else None
                package_part = self.parts.get(f"part:{package_folder}") if package_folder else None
                if package_part is not None:
                    self.connect(
                        app.id,
                        RelationKind.DEPENDS_ON,
                        package_part.id,
                        "depends on",
                        {"evidence": "explicit", "source": f"{path}#L{number}"},
                        (Source(path, number, number),),
                    )

    def part_of(self, entity: Entity) -> Part | None:
        if entity.kind is EntityKind.PROJECT:
            return self.parts.get(f"part:{_location(entity.id) or '.'}")
        path = entity.sources[0].path if entity.sources else _location(entity.id)
        return self.owner(path)

    def contracts(self) -> None:
        by_blob: dict[str, list[str]] = {}
        for path, blob in self.files.items():
            by_blob.setdefault(blob, []).append(path)
        contracts = [part for part in self.parts.values() if part.kind == "contract"]
        named = self.mentions({contract.file for contract in contracts})
        for contract in contracts:
            evidence: dict[str, tuple[str, int | None]] = {}
            holder = self.owner(contract.file)
            if holder is not None and holder.folder:
                evidence.setdefault(holder.id, (contract.file, None))
            for copy in by_blob.get(self.files.get(contract.file, ""), []):
                if copy != contract.file and (part := self.owner(copy)) is not None:
                    evidence.setdefault(part.id, (copy, None))
            for part_id, config, number in named.get(contract.file, []):
                evidence.setdefault(part_id, (config, number))
            for part_id, (path, line) in sorted(evidence.items()):
                part = self.parts[part_id]
                kind = RelationKind.IMPLEMENTS if part.kind == "service" else RelationKind.CALLS_VIA
                self.connect(
                    part_id,
                    kind,
                    contract.id,
                    "implements" if part.kind == "service" else "calls via",
                    {"evidence": "explicit", "source": f"{path}#L{line}" if line else path},
                    (Source(path, line, line) if line else Source(path),),
                )

    def mentions(self, targets: set[str]) -> dict[str, list[tuple[str, str, int]]]:
        """For each target file, the parts whose config files name it: (part id, config file, first line).

        Each config file is read and split once, whatever the number of targets, and only a token ending in a
        target's file name is resolved, so the cost grows with the repository's size, not with its contracts.
        """
        names = {PurePosixPath(target).name for target in targets}
        found: dict[str, list[tuple[str, str, int]]] = {}
        for path in sorted(self.files):
            part = self.owner(path) if _config_file(PurePosixPath(path).name) else None
            content = self.read(path) if part is not None else None
            if part is None or content is None:
                continue
            folder = _folder(str(PurePosixPath(path).parent))
            seen: set[str] = set()
            for number, line in enumerate(content.decode("utf-8", "replace").splitlines(), start=1):
                for mention in TOKEN_SPLIT.split(line):  # one linear split; no backtracking pattern over long lines
                    if not mention or mention.rpartition("/")[2] not in names:
                        continue
                    resolved = _normal(folder, mention)
                    for target in (mention, resolved):
                        if target in targets and target not in seen:  # set lookups: no work grows with the contracts
                            seen.add(target)
                            found.setdefault(target, []).append((part.id, path, number))
        return found

    def deployments(self) -> None:
        for entity in self.entities.values():
            if entity.kind is EntityKind.WORKER:
                part = self.owner(_location(entity.id)) or self.parts.get(f"part:{_location(entity.id) or '.'}")
                if part is not None:
                    self.connect(
                        part.id,
                        RelationKind.DEPLOYED_ON,
                        self.platform("cloudflare"),
                        "deployed on",
                        {"evidence": "explicit", "source": _cite(entity.sources[0])},
                        entity.sources[:1],
                    )
            elif entity.kind is EntityKind.DEPLOYMENT and "folder" in entity.attributes:
                key = str(entity.attributes.get("kind"))
                part = self.owner(str(entity.attributes["folder"]))
                if part is not None and key in PLATFORMS and part.kind not in ("infrastructure",):
                    self.connect(
                        part.id,
                        RelationKind.DEPLOYED_ON,
                        self.platform(key),
                        "deployed on",
                        {"evidence": "explicit", "source": _cite(entity.sources[0])},
                        entity.sources[:1],
                    )
            elif entity.kind is EntityKind.RESOURCE:
                kind = str(entity.attributes.get("type", ""))
                module = self.parts.get(f"part:{_folder(str(entity.attributes.get('module', ''))) or '.'}")
                if module is not None and module.kind != "infrastructure":
                    module = None  # its folder's id went to a code part: no arrow on a guess
                platform_key = next(
                    (value for prefix, value in RESOURCE_PLATFORMS.items() if kind.startswith(prefix)), None
                )
                if module is not None and platform_key is not None:
                    self.connect(
                        module.id,
                        RelationKind.DEPLOYED_ON,
                        self.platform(platform_key),
                        "deployed on",
                        {"evidence": "explicit", "source": _cite(entity.sources[0])},
                        entity.sources[:1],
                    )
                for path in entity.attributes.get("paths", []) or []:
                    part = self.owner(str(path))
                    if module is not None and part is not None and part.kind not in ("infrastructure",):
                        self.connect(
                            part.id,
                            RelationKind.DEPLOYED_ON,
                            module.id,
                            "deployed on",
                            {"evidence": "explicit", "source": _cite(entity.sources[0])},
                            entity.sources[:1],
                        )
        self.matched()

    def matched(self) -> None:
        """Runtime services named after a part's folder: a dashed arrow, with the rule and both names."""
        candidates: list[tuple[str, str, Source]] = []  # (service name, platform key, source)
        for entity in self.entities.values():
            kind = str(entity.attributes.get("type", ""))
            if entity.kind is EntityKind.RESOURCE and kind in NAMED_SERVICES:
                key = next(value for prefix, value in RESOURCE_PLATFORMS.items() if kind.startswith(prefix))
                candidates.append((str(entity.attributes.get("name", "")), key, entity.sources[0]))
            elif entity.kind is EntityKind.DEPLOYMENT and entity.attributes.get("target"):
                key = str(entity.attributes.get("kind"))
                if key in PLATFORMS:
                    candidates.append((str(entity.attributes["target"]), key, entity.sources[0]))
        by_word: dict[str, dict[str, tuple[str, Source]]] = {}  # word -> platform -> its first name and source
        for name, key, source in candidates:  # each name split once, then each part is one lookup per platform
            for word in set(WORD.findall(name.lower())):
                by_word.setdefault(word, {}).setdefault(key, (name, source))
        for part in [part for part in self.parts.values() if part.kind in ("service", "app") and part.folder]:
            for key, (name, source) in by_word.get(PurePosixPath(part.folder).name.lower(), {}).items():
                platform = self.platform(key)
                if (part.id, str(RelationKind.DEPLOYED_ON), platform) in self.seen:
                    continue
                self.connect(
                    part.id,
                    RelationKind.DEPLOYED_ON,
                    platform,
                    "matched by name",
                    {"evidence": "matched", "rule": "name", "names": [name, part.folder], "source": _cite(source)},
                    (source,),
                )

    def facts(self) -> SystemFacts:
        entities = [
            Entity(
                part.id,
                EntityKind.PART,
                {"kind": part.kind, "name": part.name, "folder": part.folder},
                (part.source,) if part.source.path else (),
            )
            for part in sorted(self.parts.values(), key=lambda part: part.id)
        ]
        return SystemFacts(entities, sorted(self.out, key=lambda relation: relation.key), self.warnings, self.counts)


def _config_file(name: str) -> bool:
    """A file that may name a contract: a known build file, any TOML, or an OpenAPI generator's configuration."""
    if name in CONFIG_NAMES or name.endswith(".toml"):
        return True
    head, found, tail = name.rpartition("generator")
    if not found or not (match := GENERATOR_TAIL.fullmatch(tail)):
        return False
    return match.group().endswith((".yml", ".yaml")) or head.endswith(".openapi-")


def _pbxproj_package(line: str) -> str | None:
    """The package path in an Xcode `relativePath = <path>;` line, read without a pattern that could backtrack."""
    key, equals, value = line.strip().partition("=")
    value = value.strip()
    if not equals or key.strip() != "relativePath" or not value.endswith(";"):
        return None
    value = value[:-1].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return value if value and '"' not in value and ";" not in value else None


def _folder(folder: str) -> str:
    return "" if folder in (".", "") else folder


def _cite(source: Source) -> str:
    return f"{source.path}#L{source.start_line}" if source.start_line else source.path


def derive(
    entities: Iterable[Entity],
    relations: Iterable[Relation],
    files: Mapping[str, str],
    read: Callable[[str], bytes | None],
) -> SystemFacts:
    """Parts and connections from the extracted facts; `files` maps each allowed path to its git blob."""
    system = _System(entities, relations, files, read)
    for step in (system.find_parts, system.depends, system.contracts, system.deployments):
        try:
            step()
        except Exception as error:  # the pass never fails an update
            system.warnings.append(f"system: a rule was skipped ({type(error).__name__})")
    return system.facts()
