"""The fact store: one row per fact version, valid over a range of snapshots (design section 4.2; ADR 0003)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    return FactStore(connect(tmp_path / "codetrail.db"))


def module(path: str, name: str, line: int = 1) -> Entity:
    return Entity(f"module:{path}", EntityKind.MODULE, {"name": name}, (Source(path, line, line),))


def imports(source: str, target: str) -> Relation:
    return Relation(f"module:{source}", RelationKind.IMPORTS, f"module:{target}", {}, (Source(source, 1, 1),))


A, B = module("app/a.py", "app.a"), module("app/b.py", "app.b")


def rows(store: FactStore) -> int:
    connection = store.connection
    return int(
        connection.execute("SELECT (SELECT count(*) FROM entities) + (SELECT count(*) FROM relations)").fetchone()[0]
    )


def test_the_first_record_adds_everything(store: FactStore) -> None:
    snapshot, diff = store.record("c1", [A, B], [imports("app/a.py", "app/b.py")])
    assert snapshot.commit == "c1"
    assert sorted(diff.added_entities) == ["module:app/a.py", "module:app/b.py"]
    assert diff.added_relations == [("module:app/a.py", "imports", "module:app/b.py")]
    assert store.entity("module:app/a.py") == A
    assert store.latest_snapshot() == snapshot


def test_recording_the_same_facts_changes_nothing(store: FactStore) -> None:
    store.record("c1", [A, B], [imports("app/a.py", "app/b.py")])
    before = rows(store)
    _, diff = store.record("c2", [A, B], [imports("app/a.py", "app/b.py")])
    assert diff.is_empty
    assert rows(store) == before


def test_a_changed_attribute_opens_a_new_version(store: FactStore) -> None:
    store.record("c1", [A], [])
    renamed = Entity(A.id, A.kind, {"name": "app.alpha"}, A.sources)
    snapshot, diff = store.record("c2", [renamed], [])
    assert diff.changed_entities == [A.id]
    assert store.entity(A.id) == renamed
    versions = store.connection.execute(
        "SELECT first_seen, last_seen FROM entities WHERE id = ? ORDER BY first_seen", (A.id,)
    ).fetchall()
    first = store.previous_snapshot(snapshot)
    assert first is not None
    assert [tuple(row) for row in versions] == [(first.id, first.id), (snapshot.id, None)]


def test_a_missing_fact_is_closed(store: FactStore) -> None:
    store.record("c1", [A, B], [imports("app/a.py", "app/b.py")])
    _, diff = store.record("c2", [A], [])
    assert diff.removed_entities == [B.id]
    assert diff.removed_relations == [("module:app/a.py", "imports", "module:app/b.py")]
    assert store.entity(B.id) is None
    assert store.relations() == []


def test_a_moved_source_updates_in_place(store: FactStore) -> None:
    store.record("c1", [A], [])
    before = rows(store)
    moved = module("app/a.py", "app.a", line=40)
    _, diff = store.record("c2", [moved], [])
    assert diff.is_empty
    assert rows(store) == before
    assert store.entity(A.id) == moved


def test_a_removed_fact_can_come_back(store: FactStore) -> None:
    store.record("c1", [A], [])
    store.record("c2", [], [])
    _, diff = store.record("c3", [A], [])
    assert diff.added_entities == [A.id]
    assert store.entity(A.id) == A


def test_the_diff_of_an_older_snapshot_is_still_available(store: FactStore) -> None:
    first, _ = store.record("c1", [A], [])
    store.record("c2", [A, B], [])
    assert store.diff(first).added_entities == [A.id]


def test_queries_filter_by_kind(store: FactStore) -> None:
    project = Entity("project:.", EntityKind.PROJECT, {"name": "app"}, (Source("pyproject.toml"),))
    store.record("c1", [A, project], [])
    assert store.entities(EntityKind.PROJECT) == [project]
    assert len(store.entities()) == 2


def test_hashes_ignore_sources() -> None:
    assert A.hash == module("app/a.py", "app.a", line=99).hash
    assert A.hash != Entity(A.id, A.kind, {"name": "other"}, A.sources).hash


def test_facts_read_as_they_were_at_an_older_snapshot(store: FactStore) -> None:
    first, _ = store.record("c1", [A, B], [imports("app/a.py", "app/b.py")])
    renamed = module("app/a.py", "app.renamed")
    store.record("c2", [renamed], [])
    assert store.entities_at(first.id) == [A, B]
    assert [relation.key for relation in store.relations_at(first.id)] == [
        ("module:app/a.py", "imports", "module:app/b.py")
    ]
    latest = store.latest_snapshot()
    assert latest is not None and store.entities_at(latest.id) == [renamed] and store.relations_at(latest.id) == []
