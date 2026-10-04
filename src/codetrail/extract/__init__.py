"""Extractors turn a target's files into facts (design section 5).

An extractor sees one file at a time (`extract`), after being told once which files it will see (`prepare`), and
then turns the references it found into relations against every entity known from all extractors (`resolve`).
Per-file extraction keeps a later cache keyed by blob and extractor version possible without changing this interface.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

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


def run_extractors(source: Path, paths: Iterable[str], extractors: Sequence[Extractor]) -> Extraction:
    """Runs every extractor over the listed files of `source`; nothing outside the list is read."""
    listed = sorted(paths)
    warnings: list[str] = []
    entities: dict[str, Entity] = {}
    found: dict[str, list[FileFacts]] = {}
    for extractor in extractors:
        handled = [path for path in listed if extractor.handles(path)]
        extractor.prepare(handled)
        found[extractor.name] = []
        for path in handled:
            try:
                facts = extractor.extract(path, (source / PurePosixPath(path)).read_bytes())
            except Exception as error:
                warnings.append(f"{extractor.name}: {path}: could not be read ({type(error).__name__})")
                continue
            found[extractor.name].append(facts)
            for entity in facts.entities:
                _merge(entities, entity, extractor.name, path, warnings)
    relations: dict[tuple[str, str, str], Relation] = {}
    unresolved: dict[str, int] = {}
    for extractor in extractors:
        resolution = extractor.resolve(found[extractor.name], entities)
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
