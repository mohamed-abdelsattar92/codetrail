"""Facts a document mentions, and facts the guide explains (design section 18.1).

A mention isn't a reason: this only shows which dependencies, projects and resources some document names. Names are
looked up in the set of a document's words, so no regular expression is ever built from a name a repository controls.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from codetrail.facts import Entity, EntityKind
from codetrail.generate.outline import OutlineEntry
from codetrail.generate.scope import entities_in_scope
from codetrail.guide import Page
from codetrail.metrics.rationale import guide_pages

MENTIONABLE = (EntityKind.PACKAGE, EntityKind.PROJECT, EntityKind.RESOURCE)
WORD = re.compile(r"[A-Za-z0-9@/._-]+")
SEPARATORS = re.compile(r"[-_.]+")


def normalise(word: str) -> str:
    """Lower-cased, with each run of `-`, `_` and `.` made one `-`, as Python distribution names are compared."""
    return SEPARATORS.sub("-", word.lower()).strip("-/")


def words_of(text: str) -> set[str]:
    """Every word of the text, and each part of a word with slashes, normalised."""
    words = set()
    for token in WORD.findall(text):
        words.add(normalise(token))
        words.update(normalise(part) for part in token.split("/"))
    words.discard("")
    return words


def mention_name(entity: Entity) -> str:
    """What a document names: a package's name, a project's folder, a resource's address."""
    key = entity.id.split(":", 1)[1]
    if entity.kind == EntityKind.PACKAGE:
        return normalise(key.split("/", 1)[-1])  # after the ecosystem: pypi/, npm/, swift/
    if entity.kind == EntityKind.RESOURCE:
        return normalise(key.rsplit("/", 1)[-1])
    return normalise(key)


def _folder(path: str) -> str:
    parent = str(PurePosixPath(path).parent)
    return "" if parent == "." else parent


@dataclass(frozen=True)
class MentionMetric:
    mentioned: list[Entity]
    unmentioned: list[Entity]


def measure_mentions(entities: Iterable[Entity], documents: Mapping[str, str]) -> MentionMetric:
    words: set[str] = set()
    for text in documents.values():
        words |= words_of(text)
    readme_folders = {_folder(path) for path in documents if PurePosixPath(path).name.lower().startswith("readme")}
    mentioned: list[Entity] = []
    unmentioned: list[Entity] = []
    for entity in sorted(entities, key=lambda found: found.id):
        if entity.kind not in MENTIONABLE:
            continue
        if entity.kind == EntityKind.PROJECT:
            folder = entity.id.split(":", 1)[1]
            found = (("" if folder == "." else folder) in readme_folders) or (folder != "." and
                                                                             mention_name(entity) in words)  # fmt: skip
        else:
            found = mention_name(entity) in words
        (mentioned if found else unmentioned).append(entity)
    return MentionMetric(mentioned, unmentioned)


def _strings(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def page_scope(page: Page) -> OutlineEntry | None:
    """The scope a written page records (design section 4.3), or None when its front matter isn't usable."""
    scope = page.meta.get("scope", {})
    facts = page.meta.get("facts", [])
    if not isinstance(scope, dict) or not isinstance(facts, list):
        return None
    paths, kinds = _strings(scope.get("paths", [])), _strings(scope.get("kinds", []))
    if paths is None or kinds is None or not all(isinstance(fact, dict) and isinstance(fact.get("id"), str)
                                                 for fact in facts):  # fmt: skip
        return None
    return OutlineEntry(page.id, page.kind, page.title, paths, kinds, [fact["id"] for fact in facts])


@dataclass(frozen=True)
class ExplainedMetric:
    explained: int
    total: int
    unexplained: list[Entity]


def measure_explained(entities: list[Entity], pages: Iterable[Page]) -> ExplainedMetric:
    explained: set[str] = set()
    for page in guide_pages(pages):
        entry = page_scope(page)
        if entry is not None:
            explained.update(entity.id for entity in entities_in_scope(entities, entry))
    unexplained = sorted((entity for entity in entities if entity.id not in explained), key=lambda found: found.id)
    return ExplainedMetric(len(entities) - len(unexplained), len(entities), unexplained)
