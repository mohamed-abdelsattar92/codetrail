"""The outline: every page's id, kind, title and scope (design section 6.2).

Claude proposes it; Codetrail keeps only entries it can check: a valid id for the kind, a scope that holds visible
files, and fact ids that exist. The founder may edit `outline.yaml`; Claude only adds to it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from codetrail.facts import EntityKind
from codetrail.facts.store import FactStore
from codetrail.guide import PAGE_ID
from codetrail.repo.source import SourceManifest

KIND_FOLDERS = {"area": "areas", "concept": "concepts"}


CONTROL_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")  # characters a terminal acts on, or that hide text


@dataclass(frozen=True)
class OutlineEntry:
    id: str
    kind: str
    title: str
    scope_paths: list[str]
    scope_kinds: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)


def in_scope(path: str, scope_paths: Iterable[str]) -> bool:
    for scope in scope_paths:
        scope = scope.strip("/")
        if not scope or path == scope or path.startswith(scope + "/"):
            return True
    return False


def validate_outline(
    raw: Sequence[Mapping[str, Any]], store: FactStore, manifest: SourceManifest, existing: Sequence[OutlineEntry] = ()
) -> tuple[list[OutlineEntry], list[str]]:
    """The entries that pass, and a problem for each one dropped or trimmed."""
    entries: list[OutlineEntry] = []
    problems: list[str] = []
    seen = {entry.id for entry in existing}
    kinds = {str(kind) for kind in EntityKind}
    for item in raw:
        page_id = str(item.get("id", "")).strip().lower()
        kind = str(item.get("kind", ""))
        if (
            kind not in KIND_FOLDERS
            or not PAGE_ID.fullmatch(page_id)
            or not page_id.startswith(KIND_FOLDERS[kind] + "/")
        ):
            problems.append(f"{page_id!r}: the id must be {KIND_FOLDERS.get(kind, 'areas or concepts')}/<slug>.")
            continue
        if page_id in seen:
            problems.append(f"{page_id}: the id is already used.")
            continue
        scope = [str(path).strip("/") for path in item.get("scope_paths", []) if isinstance(path, str)]
        scope = [path for path in scope if any(in_scope(file, [path]) for file in manifest.files)]
        if not scope:
            problems.append(f"{page_id}: none of its scope paths holds a visible file.")
            continue
        facts = [str(fact) for fact in item.get("facts", []) if isinstance(fact, str)]
        known = [fact for fact in facts if store.entity(fact) is not None]
        if len(known) != len(facts):
            problems.append(f"{page_id}: dropped facts that don't exist: {sorted(set(facts) - set(known))}.")
        scope_kinds = [str(kind) for kind in item.get("scope_kinds", []) if str(kind) in kinds]
        title = str(item.get("title", page_id)).strip()
        if any(unicodedata.category(character) in CONTROL_CATEGORIES for character in title):
            problems.append(f"{page_id}: the title holds a control character.")  # a terminal would act on it
            continue
        entries.append(OutlineEntry(page_id, kind, title, scope, scope_kinds, known))
        seen.add(page_id)
    return entries, problems


def outline_entries(data: Mapping[str, Any] | None) -> list[OutlineEntry]:
    """The entries of a stored outline, skipping any that are malformed (the founder may have edited it)."""
    entries = []
    for item in (data or {}).get("pages", []) or []:
        if isinstance(item, Mapping) and PAGE_ID.fullmatch(str(item.get("id", ""))):
            entries.append(
                OutlineEntry(
                    str(item["id"]),
                    str(item.get("kind", "")),
                    str(item.get("title", item["id"])),
                    [str(path) for path in item.get("scope_paths", []) or []],
                    [str(kind) for kind in item.get("scope_kinds", []) or []],
                    [str(fact) for fact in item.get("facts", []) or []],
                )
            )
    return entries


@dataclass(frozen=True)
class OutlinePath:
    id: str
    title: str
    goal: str
    steps: list[str]


def outline_data(entries: Iterable[OutlineEntry], paths: Iterable[OutlinePath] = ()) -> dict[str, Any]:
    return {"pages": [asdict(entry) for entry in entries], "paths": [asdict(path) for path in paths]}


def validate_paths(
    raw: Sequence[Mapping[str, Any]], page_ids: set[str], existing: Sequence[OutlinePath] = ()
) -> tuple[list[OutlinePath], list[str]]:
    """Paths whose id is valid and that keep at least one step naming an existing page."""
    paths: list[OutlinePath] = []
    problems: list[str] = []
    seen = {path.id for path in existing}
    for item in raw:
        path_id = str(item.get("id", "")).strip().lower()
        if not path_id.startswith("paths/") or not PAGE_ID.fullmatch(path_id) or path_id in seen:
            problems.append(f"{path_id!r}: a path id must be a new paths/<slug>.")
            continue
        steps = [str(step) for step in item.get("steps", []) if isinstance(step, str)]
        kept = [step for step in steps if step in page_ids]
        if len(kept) != len(steps):
            problems.append(f"{path_id}: dropped steps that aren't pages: {sorted(set(steps) - set(kept))}.")
        if not kept:
            problems.append(f"{path_id}: no step names an existing page.")
            continue
        paths.append(OutlinePath(path_id, str(item.get("title", path_id)), str(item.get("goal", "")), kept))
        seen.add(path_id)
    return paths, problems


def outline_paths(data: Mapping[str, Any] | None, page_ids: set[str]) -> list[OutlinePath]:
    """The stored paths, each keeping only steps that are still pages."""
    paths = []
    for item in (data or {}).get("paths", []) or []:
        path_id = str(item.get("id", "")) if isinstance(item, Mapping) else ""
        if isinstance(item, Mapping) and path_id.startswith("paths/") and PAGE_ID.fullmatch(path_id):
            steps = [str(step) for step in item.get("steps", []) or [] if str(step) in page_ids]
            if steps:
                paths.append(OutlinePath(str(item["id"]), str(item.get("title", "")), str(item.get("goal", "")), steps))
    return paths


def uncovered_facts(store: FactStore, entries: Sequence[OutlineEntry]) -> list[str]:
    """Projects, modules and decisions that no page's scope covers."""
    named = {fact for entry in entries for fact in entry.facts}
    uncovered = []
    for entity in store.entities():
        if entity.kind is EntityKind.PACKAGE or entity.id in named:
            continue
        paths = [source.path for source in entity.sources]
        if not any(in_scope(path, entry.scope_paths) for path in paths for entry in entries):
            uncovered.append(entity.id)
    return sorted(uncovered)
