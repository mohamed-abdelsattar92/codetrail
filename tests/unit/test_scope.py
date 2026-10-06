"""What changed in a page's scope since it was written, for revising it (design sections 4.3 and 6.3)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore
from codetrail.generate.outline import OutlineEntry
from codetrail.generate.scope import page_snapshot, scope_changes
from codetrail.guide import Page


def module(path: str, name: str) -> Entity:
    return Entity(f"module:{path}", EntityKind.MODULE, {"name": name}, (Source(path, 1, 1),))


def imports(source: str, target: str) -> Relation:
    return Relation(f"module:{source}", RelationKind.IMPORTS, f"module:{target}", {}, (Source(source, 1, 1),))


ENTRY = OutlineEntry("areas/app", "area", "The app", ["app"])


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    return FactStore(connect(tmp_path / "codetrail.db"))


def test_the_changes_since_a_snapshot_name_each_fact_and_link_in_scope(store: FactStore) -> None:
    a, b, outside = module("app/a.py", "app.a"), module("app/b.py", "app.b"), module("lib/x.py", "lib.x")
    first, _ = store.record("c1", [a, b, outside], [])
    c = module("app/c.py", "app.c")
    store.record("c2", [module("app/a.py", "app.alpha"), c, module("lib/x.py", "lib.y")],
                 [imports("app/a.py", "app/c.py")])  # fmt: skip
    assert scope_changes(store, ENTRY, first.id) == [
        "added link module:app/a.py imports module:app/c.py",
        'added module module:app/c.py {"name": "app.c"}',
        'changed module module:app/a.py {"name": "app.alpha"}',
        "removed module module:app/b.py",
    ]


def test_nothing_changed_means_no_lines(store: FactStore) -> None:
    first, _ = store.record("c1", [module("app/a.py", "app.a")], [])
    assert scope_changes(store, ENTRY, first.id) == []


def test_a_page_records_its_snapshot_and_older_pages_fall_back_to_the_first_at_their_commit(store: FactStore) -> None:
    first, _ = store.record("c1", [module("app/a.py", "app.a")], [])
    store.record("c1", [module("app/a.py", "app.alpha")], [])  # a newer Codetrail, the same commit
    assert page_snapshot(store, Page("areas/app", {"snapshot": 2, "built_at": "c1"})) == 2
    assert page_snapshot(store, Page("areas/app", {"built_at": "c1"})) == first.id  # the earliest: misses nothing
    assert page_snapshot(store, Page("areas/app", {"built_at": "unknown"})) is None
