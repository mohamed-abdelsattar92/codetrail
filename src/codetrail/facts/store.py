"""The fact store: each fact version is one row, valid from `first_seen` to `last_seen` (design section 4.2; ADR 0003).

An update writes only what changed: new rows for new and changed facts, a closed range for removed ones, and fresh
sources in place for unchanged facts. The diff for a snapshot is a query on the ranges that open or close at it.
Table and column names come only from this module's constants; values are always parameters.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from codetrail.facts import Entity, EntityKind, FactDiff, Relation, RelationKind, Snapshot, Source

ENTITY_KEY = ("id",)
RELATION_KEY = ("source_id", "kind", "target_id")


def _sources_json(sources: tuple[Source, ...]) -> str:
    return json.dumps([[source.path, source.start_line, source.end_line] for source in sources])


def _sources(text: str) -> tuple[Source, ...]:
    return tuple(Source(path, start, end) for path, start, end in json.loads(text))


class FactStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record(
        self, commit: str, entities: Iterable[Entity], relations: Iterable[Relation]
    ) -> tuple[Snapshot, FactDiff]:
        """Records the facts found at `commit` as a new snapshot and returns it with its diff."""
        connection = self.connection
        connection.execute("BEGIN")
        try:
            taken_at = datetime.now(UTC).isoformat(timespec="seconds")
            cursor = connection.execute(
                "INSERT INTO snapshots (commit_sha, taken_at) VALUES (?, ?)", (commit, taken_at)
            )
            snapshot = Snapshot(int(cursor.lastrowid or 0), commit, taken_at)
            previous = self.previous_snapshot(snapshot)
            closing = previous.id if previous else None
            entity_rows: dict[tuple[str, ...], tuple[str, dict[str, Any]]] = {
                (entity.id,): (entity.hash, {"kind": str(entity.kind), "attributes": _json(entity.attributes),
                                             "sources": _sources_json(entity.sources)})
                for entity in entities
            }  # fmt: skip
            relation_rows: dict[tuple[str, ...], tuple[str, dict[str, Any]]] = {
                relation.key: (relation.hash, {"attributes": _json(relation.attributes),
                                               "sources": _sources_json(relation.sources)})
                for relation in relations
            }  # fmt: skip
            self._write("entities", ENTITY_KEY, entity_rows, snapshot.id, closing)
            self._write("relations", RELATION_KEY, relation_rows, snapshot.id, closing)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return snapshot, self.diff(snapshot)

    def _write(
        self,
        table: str,
        key_columns: Sequence[str],
        rows: dict[tuple[str, ...], tuple[str, dict[str, Any]]],
        snapshot_id: int,
        closing: int | None,
    ) -> None:
        keys = ", ".join(key_columns)
        current = {
            tuple(row[column] for column in key_columns): (row["rowid"], row["hash"])
            for row in self.connection.execute(f"SELECT rowid, hash, {keys} FROM {table} WHERE last_seen IS NULL")
        }
        for key, (fact_hash, values) in rows.items():
            existing = current.pop(key, None)
            if existing is not None and existing[1] == fact_hash:
                self.connection.execute(
                    f"UPDATE {table} SET sources = ? WHERE rowid = ?",
                    (values["sources"], existing[0]),
                )
                continue
            if existing is not None:
                self.connection.execute(f"UPDATE {table} SET last_seen = ? WHERE rowid = ?", (closing, existing[0]))
            columns = (*key_columns, *values, "hash", "first_seen")
            self.connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                (*key, *values.values(), fact_hash, snapshot_id),
            )
        for rowid, _hash in current.values():  # facts no longer found
            self.connection.execute(f"UPDATE {table} SET last_seen = ? WHERE rowid = ?", (closing, rowid))

    def latest_snapshot(self) -> Snapshot | None:
        row = self.connection.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        return Snapshot(row["id"], row["commit_sha"], row["taken_at"]) if row else None

    def first_snapshot(self, commit: str) -> Snapshot | None:
        """The earliest snapshot taken at the commit (a newer Codetrail can take more at the same one)."""
        row = self.connection.execute(
            "SELECT * FROM snapshots WHERE commit_sha = ? ORDER BY id LIMIT 1", (commit,)
        ).fetchone()
        return Snapshot(row["id"], row["commit_sha"], row["taken_at"]) if row else None

    def previous_snapshot(self, snapshot: Snapshot) -> Snapshot | None:
        row = self.connection.execute(
            "SELECT * FROM snapshots WHERE id < ? ORDER BY id DESC LIMIT 1", (snapshot.id,)
        ).fetchone()
        return Snapshot(row["id"], row["commit_sha"], row["taken_at"]) if row else None

    def diff(self, snapshot: Snapshot) -> FactDiff:
        previous = self.previous_snapshot(snapshot)
        closed_at = previous.id if previous else -1
        entities = self._opened_and_closed("entities", ENTITY_KEY, snapshot.id, closed_at)
        relations = self._opened_and_closed("relations", RELATION_KEY, snapshot.id, closed_at)
        return FactDiff(
            added_entities=sorted(key[0] for key in entities[0] - entities[1]),
            changed_entities=sorted(key[0] for key in entities[0] & entities[1]),
            removed_entities=sorted(key[0] for key in entities[1] - entities[0]),
            added_relations=sorted(_relation_key(key) for key in relations[0] - relations[1]),
            changed_relations=sorted(_relation_key(key) for key in relations[0] & relations[1]),
            removed_relations=sorted(_relation_key(key) for key in relations[1] - relations[0]),
        )

    def _opened_and_closed(
        self, table: str, key_columns: Sequence[str], opened_at: int, closed_at: int
    ) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
        keys = ", ".join(key_columns)
        opened = {tuple(row) for row in self.connection.execute(
            f"SELECT {keys} FROM {table} WHERE first_seen = ?", (opened_at,))}  # fmt: skip
        closed = {tuple(row) for row in self.connection.execute(
            f"SELECT {keys} FROM {table} WHERE last_seen = ?", (closed_at,))}  # fmt: skip
        return opened, closed

    def snapshots(self) -> list[Snapshot]:
        """Every snapshot, oldest first."""
        rows = self.connection.execute("SELECT * FROM snapshots ORDER BY id")
        return [Snapshot(row["id"], row["commit_sha"], row["taken_at"]) for row in rows]

    def entity_counts(self, snapshot_ids: Sequence[int]) -> dict[int, dict[str, int]]:
        """Each snapshot's entities by kind, from the validity ranges (design section 19.1)."""
        counts: dict[int, dict[str, int]] = {snapshot_id: {} for snapshot_id in snapshot_ids}
        marks = ", ".join("?" for _ in snapshot_ids)
        rows = self.connection.execute(
            "SELECT s.id AS snapshot, e.kind AS kind, COUNT(*) AS count FROM snapshots s JOIN entities e"
            " ON e.first_seen <= s.id AND (e.last_seen IS NULL OR e.last_seen >= s.id)"
            f" WHERE s.id IN ({marks}) GROUP BY s.id, e.kind", tuple(snapshot_ids))  # fmt: skip
        for row in rows:
            counts[row["snapshot"]][row["kind"]] = row["count"]
        return counts

    def entity_spans(self, kind: EntityKind) -> list[tuple[str, int, int | None]]:
        """Each entity of the kind ever seen: its first snapshot, and its last unless it is still current."""
        rows = self.connection.execute(
            "SELECT id, MIN(first_seen) AS first, CASE WHEN SUM(last_seen IS NULL) > 0 THEN NULL"
            " ELSE MAX(last_seen) END AS last FROM entities WHERE kind = ? GROUP BY id ORDER BY id",
            (str(kind),),
        )
        return [(row["id"], row["first"], row["last"]) for row in rows]

    def entities(self, kind: EntityKind | None = None) -> list[Entity]:
        query = "SELECT * FROM entities WHERE last_seen IS NULL" + (" AND kind = ?" if kind else "") + " ORDER BY id"
        return [_entity(row) for row in self.connection.execute(query, (str(kind),) if kind else ())]

    def relations(self, kind: RelationKind | None = None) -> list[Relation]:
        query = "SELECT * FROM relations WHERE last_seen IS NULL" + (" AND kind = ?" if kind else "")
        query += " ORDER BY source_id, kind, target_id"
        return [_relation(row) for row in self.connection.execute(query, (str(kind),) if kind else ())]

    def entities_at(self, snapshot_id: int) -> list[Entity]:
        """Every entity as it was at the snapshot."""
        query = ("SELECT * FROM entities WHERE first_seen <= ? AND (last_seen IS NULL OR last_seen >= ?)"
                 " ORDER BY id")  # fmt: skip
        return [_entity(row) for row in self.connection.execute(query, (snapshot_id, snapshot_id))]

    def relations_at(self, snapshot_id: int) -> list[Relation]:
        """Every relation as it was at the snapshot."""
        query = ("SELECT * FROM relations WHERE first_seen <= ? AND (last_seen IS NULL OR last_seen >= ?)"
                 " ORDER BY source_id, kind, target_id")  # fmt: skip
        return [_relation(row) for row in self.connection.execute(query, (snapshot_id, snapshot_id))]

    def entity(self, entity_id: str) -> Entity | None:
        row = self.connection.execute(
            "SELECT * FROM entities WHERE id = ? AND last_seen IS NULL", (entity_id,)
        ).fetchone()
        return _entity(row) if row else None


def _json(attributes: Any) -> str:
    return json.dumps(dict(attributes), sort_keys=True, ensure_ascii=False)


def _relation_key(key: tuple[str, ...]) -> tuple[str, str, str]:
    return (key[0], key[1], key[2])


def _entity(row: sqlite3.Row) -> Entity:
    return Entity(row["id"], EntityKind(row["kind"]), json.loads(row["attributes"]), _sources(row["sources"]))


def _relation(row: sqlite3.Row) -> Relation:
    return Relation(
        row["source_id"], RelationKind(row["kind"]), row["target_id"], json.loads(row["attributes"]),
        _sources(row["sources"]),
    )  # fmt: skip
