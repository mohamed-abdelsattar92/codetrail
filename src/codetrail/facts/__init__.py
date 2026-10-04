"""Facts: deterministic statements about a target, produced only by extractors (design section 4.1).

An entity is one thing in the repository; a relation is a typed edge between two entities. Ids are a kind plus a
natural key, never a line number. A fact's hash covers its kind, key and attributes, not where it was found.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EntityKind(StrEnum):
    PROJECT = "project"
    MODULE = "module"
    PACKAGE = "package"
    DECISION = "decision"


class RelationKind(StrEnum):
    CONTAINS = "contains"
    IMPORTS = "imports"
    DEPENDS_ON = "depends_on"
    SUPERSEDES = "supersedes"


@dataclass(frozen=True)
class Source:
    path: str
    start_line: int | None = None
    end_line: int | None = None


def _hash(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class Entity:
    id: str
    kind: EntityKind
    attributes: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[Source, ...] = ()

    @property
    def hash(self) -> str:
        return _hash(str(self.kind), self.id, dict(self.attributes))


@dataclass(frozen=True)
class Relation:
    source_id: str
    kind: RelationKind
    target_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[Source, ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source_id, str(self.kind), self.target_id)

    @property
    def hash(self) -> str:
        return _hash(*self.key, dict(self.attributes))


@dataclass(frozen=True)
class Snapshot:
    id: int
    commit: str
    taken_at: str


@dataclass(frozen=True)
class FactDiff:
    added_entities: list[str] = field(default_factory=list)
    changed_entities: list[str] = field(default_factory=list)
    removed_entities: list[str] = field(default_factory=list)
    added_relations: list[tuple[str, str, str]] = field(default_factory=list)
    changed_relations: list[tuple[str, str, str]] = field(default_factory=list)
    removed_relations: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.added_entities, self.changed_entities, self.removed_entities, self.added_relations,
             self.changed_relations, self.removed_relations)
        )  # fmt: skip

    def changed_ids(self) -> set[str]:
        """Every entity id this diff touches, including both ends of changed relations."""
        ids = set(self.added_entities) | set(self.changed_entities) | set(self.removed_entities)
        for source_id, _kind, target_id in self.added_relations + self.changed_relations + self.removed_relations:
            ids |= {source_id, target_id}
        return ids
