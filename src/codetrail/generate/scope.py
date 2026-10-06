"""What a page covers, and whether it must be rewritten (design section 4.3).

A page records the facts it explains directly with their hashes, one hash over everything in its scope, the snapshot
it was written at, and the files Claude read with their blobs. Changed facts or a changed scope make it affected;
changed files only flag it. What changed in its scope since its snapshot is what a revision is told (section 6.3).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from codetrail.facts import Entity, Relation
from codetrail.facts.store import FactStore
from codetrail.generate.outline import OutlineEntry, in_scope
from codetrail.guide import Page
from codetrail.repo.source import SourceManifest


def facts_in_scope(store: FactStore, entry: OutlineEntry) -> list[Entity]:
    return _in_scope(store.entities(), entry)


def _in_scope(entities: list[Entity], entry: OutlineEntry) -> list[Entity]:
    """The entities under the entry's scope paths (of its kinds, if it names any), and the facts it names."""
    named = set(entry.facts)
    found = []
    for entity in entities:
        kind_ok = not entry.scope_kinds or str(entity.kind) in entry.scope_kinds
        under = any(in_scope(source.path, entry.scope_paths) for source in entity.sources)
        if entity.id in named or (kind_ok and under):
            found.append(entity)
    return sorted(found, key=lambda entity: entity.id)


def page_snapshot(store: FactStore, page: Page) -> int | None:
    """The snapshot the page was written at; for a page written before pages recorded it, the earliest snapshot at
    its commit, which can only show more changes than happened since, never fewer."""
    recorded = page.meta.get("snapshot")
    if isinstance(recorded, int):
        return recorded
    found = store.first_snapshot(str(page.meta.get("built_at", "")))
    return found.id if found else None


def scope_changes(store: FactStore, entry: OutlineEntry, since: int) -> list[str]:
    """Each fact and link in the entry's scope added, changed or removed since the snapshot, one line each."""
    before = {entity.id: entity for entity in _in_scope(store.entities_at(since), entry)}
    now = {entity.id: entity for entity in _in_scope(store.entities(), entry)}
    lines = []
    for entity_id in sorted(before.keys() | now.keys()):
        old, new = before.get(entity_id), now.get(entity_id)
        if new is None and old is not None:
            lines.append(f"removed {old.kind} {entity_id}")
        elif new is not None and (old is None or old.hash != new.hash):
            attributes = json.dumps(dict(new.attributes), sort_keys=True, ensure_ascii=False)
            lines.append(f"{'added' if old is None else 'changed'} {new.kind} {entity_id} {attributes}")
    old_links = _links(store.relations_at(since), before.keys())
    new_links = _links(store.relations(), now.keys())
    for key in sorted(old_links.keys() | new_links.keys()):
        old_hash, new_hash = old_links.get(key), new_links.get(key)
        if old_hash != new_hash:
            change = "removed" if new_hash is None else "added" if old_hash is None else "changed"
            lines.append(f"{change} link {' '.join(key)}")
    return sorted(lines)


def _links(relations: list[Relation], sources: Iterable[str]) -> dict[tuple[str, str, str], str]:
    """The relations from the given entities, as scope_hash counts them."""
    ids = set(sources)
    return {relation.key: relation.hash for relation in relations if relation.source_id in ids}


def scope_hash(store: FactStore, entry: OutlineEntry) -> str:
    entities = facts_in_scope(store, entry)
    ids = {entity.id for entity in entities}
    relations = [relation for relation in store.relations() if relation.source_id in ids]
    parts = [[entity.id, entity.hash] for entity in entities] + [[list(r.key), r.hash] for r in relations]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def page_meta(entry: OutlineEntry, store: FactStore, manifest: SourceManifest, files_read: list[str]) -> dict[str, Any]:
    facts = []
    for fact in entry.facts:
        entity = store.entity(fact)
        if entity is not None:
            facts.append({"id": entity.id, "hash": entity.hash})
    snapshot = store.latest_snapshot()
    return {
        "id": entry.id,
        "kind": entry.kind,
        "title": entry.title,
        "generated": True,
        "built_at": snapshot.commit if snapshot else manifest.commit,
        "snapshot": snapshot.id if snapshot else None,
        "facts": facts,
        "scope": {"paths": entry.scope_paths, "kinds": entry.scope_kinds, "hash": scope_hash(store, entry)},
        "files": [{"path": path, "blob": manifest.files[path]} for path in files_read if path in manifest.files],
    }


def affected_reason(entry: OutlineEntry, page: Page | None, store: FactStore) -> str | None:
    """Why the page must be rewritten, or None when it is current."""
    if page is None:
        return "new"
    meta = page.meta
    scope = meta.get("scope") or {}
    if meta.get("title") != entry.title or scope.get("paths") != entry.scope_paths:
        return "outline changed"
    for fact in meta.get("facts") or []:
        entity = store.entity(str(fact.get("id")))
        if entity is None or entity.hash != fact.get("hash"):
            return "facts changed"
    if scope.get("hash") != scope_hash(store, entry):
        return "scope changed"
    return None


def changed_fact_count(entry: OutlineEntry, page: Page | None, store: FactStore) -> int:
    """How many of the page's recorded facts changed or disappeared; used to rank affected pages."""
    if page is None:
        return len(entry.facts)
    count = 0
    for fact in page.meta.get("facts") or []:
        entity = store.entity(str(fact.get("id")))
        if entity is None or entity.hash != fact.get("hash"):
            count += 1
    return count


def sources_changed(page: Page, manifest: SourceManifest) -> bool:
    """True when a file Claude read for the page has a different blob now (or is no longer visible)."""
    return any(manifest.files.get(str(item.get("path"))) != item.get("blob") for item in page.meta.get("files") or [])
