"""What a page covers, and whether it must be rewritten (design section 4.3).

A page records the facts it explains directly with their hashes, one hash over everything in its scope, and the files
Claude read with their blobs. Changed facts or a changed scope make it affected; changed files only flag it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from codetrail.facts import Entity
from codetrail.facts.store import FactStore
from codetrail.generate.outline import OutlineEntry, in_scope
from codetrail.guide import Page
from codetrail.repo.source import SourceManifest


def facts_in_scope(store: FactStore, entry: OutlineEntry) -> list[Entity]:
    found: dict[str, Entity] = {}
    for entity in store.entities():
        kind_ok = not entry.scope_kinds or str(entity.kind) in entry.scope_kinds
        if kind_ok and any(in_scope(source.path, entry.scope_paths) for source in entity.sources):
            found[entity.id] = entity
    for fact in entry.facts:
        named = store.entity(fact)
        if named is not None:
            found[named.id] = named
    return sorted(found.values(), key=lambda entity: entity.id)


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
