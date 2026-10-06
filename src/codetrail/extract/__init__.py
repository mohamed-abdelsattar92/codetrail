"""Extractors turn a target's files into facts (design section 5).

An extractor sees one file at a time (`extract`), after being told once which files it will see (`prepare`), and
then turns the references it found into relations against every entity known from all extractors (`resolve`).
Per-file extraction keeps a later cache keyed by blob and extractor version possible without changing this interface.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import yaml

from codetrail.facts import Entity, Relation, RelationKind, Source


@dataclass(frozen=True)
class Reference:
    """Something a file points at by name, which `resolve` turns into a relation if it can."""

    source_id: str
    kind: RelationKind
    target: str
    sources: tuple[Source, ...] = ()
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileFacts:
    path: str
    entities: tuple[Entity, ...] = ()
    references: tuple[Reference, ...] = ()


@dataclass(frozen=True)
class Resolution:
    relations: list[Relation]
    unresolved: int = 0


class Extractor(Protocol):
    name: str
    version: int

    def handles(self, path: str) -> bool: ...

    def prepare(self, paths: Sequence[str]) -> None: ...

    def extract(self, path: str, content: bytes) -> FileFacts: ...

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution: ...


@dataclass(frozen=True)
class Extraction:
    entities: list[Entity]
    relations: list[Relation]
    warnings: list[str]
    unresolved: dict[str, int]
    system: dict[str, int] = field(default_factory=dict)  # connections the system pass found, per rule


DEFAULT_MAX_FILE_BYTES = 1_000_000
URL_CREDENTIALS = re.compile(r"(://)[^/\s]*@")
LOCAL_SPECIFIERS = ("npm:", "workspace:", "file:", "link:", "portal:", "patch:")


def without_credentials(text: str) -> str:
    """A specifier or URL without user or password: scheme://user:pass@host and user:pass@host:path."""
    text = URL_CREDENTIALS.sub(r"\1", text)
    authority = text.split("/", 1)[0]
    if "://" not in text and "@" in authority and ":" in authority and not text.startswith(LOCAL_SPECIFIERS):
        text = text[authority.rindex("@") + 1 :]
    return text


class _NoAliases(yaml.SafeLoader):
    """safe_load, refusing YAML aliases, so a small file can't expand into an enormous one (a billion-laughs file)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            raise yaml.YAMLError("aliases are not read")
        return super().compose_node(parent, index)


def load_yaml_without_aliases(text: str) -> tuple[yaml.Node | None, Any]:
    """A target's YAML document, read in one pass: its node tree (for line numbers) and its data.

    Raises yaml.YAMLError on an alias or any tag safe_load refuses; the runner turns that into a warning.
    """
    loader = _NoAliases(text)
    try:
        root = loader.get_single_node()
        return root, loader.construct_document(root) if root is not None else None
    finally:
        loader.dispose()


DEFAULT_MAX_ATTRIBUTE_CHARS = 300


def run_extractors(
    source: Path,
    paths: Iterable[str],
    extractors: Sequence[Extractor],
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_attribute_chars: int = DEFAULT_MAX_ATTRIBUTE_CHARS,
) -> Extraction:
    """Runs every extractor over the listed files of `source`; nothing outside the list is read.

    Files over `max_file_bytes` are skipped with a warning, and facts whose attributes aren't plain JSON become a
    warning for their file, so no file can stall or break an update. Text attributes come from the target's files and
    reach prompts and pages, so each is cut to `max_attribute_chars`, and a fact whose id is longer is skipped.
    """
    listed = sorted(paths)
    warnings: list[str] = []
    entities: dict[str, Entity] = {}
    found: dict[str, list[FileFacts]] = {}
    for extractor in extractors:
        handled = [path for path in listed if extractor.handles(path)]
        found[extractor.name] = []
        try:
            extractor.prepare(handled)
        except Exception as error:  # one extractor's bad input never fails the update; its files are skipped
            warnings.append(f"{extractor.name}: could not prepare ({type(error).__name__})")
            continue
        for path in handled:
            file = source / PurePosixPath(path)
            if file.stat().st_size > max_file_bytes:
                warnings.append(f"{extractor.name}: {path}: skipped, larger than {max_file_bytes} bytes")
                continue
            try:
                facts = extractor.extract(path, file.read_bytes())
                for attributes in [entity.attributes for entity in facts.entities] + [
                    reference.attributes for reference in facts.references
                ]:
                    json.dumps(dict(attributes))
            except Exception as error:
                warnings.append(f"{extractor.name}: {path}: could not be read ({type(error).__name__})")
                continue
            facts = _cut_attributes(facts, max_attribute_chars)
            if any(len(entity.id) > max_attribute_chars for entity in facts.entities):
                warnings.append(
                    f"{extractor.name}: {path}: skipped a fact whose id is longer than {max_attribute_chars} characters"
                )
                facts = replace(facts, entities=tuple(e for e in facts.entities if len(e.id) <= max_attribute_chars))
            found[extractor.name].append(facts)
            for entity in facts.entities:
                _merge(entities, entity, extractor.name, path, warnings)
    relations: dict[tuple[str, str, str], Relation] = {}
    unresolved: dict[str, int] = {}
    for extractor in extractors:
        try:
            resolution = extractor.resolve(found[extractor.name], entities)
        except Exception as error:  # its entities stay; only its relations are lost
            warnings.append(f"{extractor.name}: could not resolve references ({type(error).__name__})")
            continue
        dangling = [r for r in resolution.relations if r.source_id not in entities or r.target_id not in entities]
        for relation in resolution.relations:
            if relation not in dangling:
                relations.setdefault(relation.key, relation)
        if resolution.unresolved or dangling:
            unresolved[extractor.name] = resolution.unresolved + len(dangling)
    return Extraction(
        entities=sorted(entities.values(), key=lambda entity: entity.id),
        relations=sorted(relations.values(), key=lambda relation: relation.key),
        warnings=warnings,
        unresolved=unresolved,
    )


def check_facts(
    entities: Sequence[Entity], relations: Sequence[Relation], limit: int
) -> tuple[list[Entity], list[Relation], list[str]]:
    """The checks `run_extractors` applies, for facts made outside an extractor (the system pass, design 17.3).

    Attributes must be plain JSON and are cut to `limit` characters; a fact whose id is longer is dropped, and so is
    every relation whose ends are no longer both kept. Warnings name no content.
    """
    kept: list[Entity] = []
    warnings: list[str] = []
    for entity in entities:
        try:
            json.dumps(dict(entity.attributes))
        except TypeError, ValueError:
            warnings.append(f"system: a fact's attributes weren't plain data ({entity.kind})")
            continue
        if len(entity.id) > limit:
            warnings.append(f"system: skipped a fact whose id is longer than {limit} characters ({entity.kind})")
            continue
        kept.append(replace(entity, attributes=_cut(entity.attributes, limit)))
    ids = {entity.id for entity in kept}
    related = []
    for relation in relations:
        try:
            json.dumps(dict(relation.attributes))
        except TypeError, ValueError:
            continue
        if relation.source_id in ids and relation.target_id in ids:
            related.append(replace(relation, attributes=_cut(relation.attributes, limit)))
    return kept, related, warnings


def _cut(attributes: Mapping[str, Any], limit: int) -> dict[str, Any]:
    def cut(value: Any) -> Any:
        if isinstance(value, str) and len(value) > limit:
            return value[: limit - 1] + "…"
        return [cut(item) for item in value] if isinstance(value, list) else value

    return {key: cut(value) for key, value in attributes.items()}


def _cut_attributes(facts: FileFacts, limit: int) -> FileFacts:
    def cut_all(attributes: Mapping[str, Any]) -> dict[str, Any]:
        return _cut(attributes, limit)

    return replace(
        facts,
        entities=tuple(replace(entity, attributes=cut_all(entity.attributes)) for entity in facts.entities),
        references=tuple(replace(item, attributes=cut_all(item.attributes)) for item in facts.references),
    )


def _merge(entities: dict[str, Entity], entity: Entity, extractor: str, path: str, warnings: list[str]) -> None:
    existing = entities.get(entity.id)
    if existing is None:
        entities[entity.id] = entity
        return
    if dict(existing.attributes) != dict(entity.attributes):
        first = existing.sources[0].path if existing.sources else "another file"
        warnings.append(f"{extractor}: {entity.id} has different attributes in {path}; kept {first}'s")
    sources = existing.sources + tuple(source for source in entity.sources if source not in existing.sources)
    entities[entity.id] = Entity(existing.id, existing.kind, existing.attributes, sources)
