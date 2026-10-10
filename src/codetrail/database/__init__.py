"""The target's SQLite database: facts and learning state, migrated with ordered SQL files (design section 10).

Each migration is `migrations/NNNN_name.sql`; `PRAGMA user_version` holds the last one applied.
"""

from __future__ import annotations

import re
import sqlite3
from importlib.resources import files
from pathlib import Path

from codetrail.errors import CodetrailError

MIGRATION_NAME = re.compile(r"(\d{4})_[a-z0-9_]+\.sql")


def migrations() -> list[tuple[int, str]]:
    found = []
    for item in files("codetrail.database").joinpath("migrations").iterdir():
        match = MIGRATION_NAME.fullmatch(item.name)
        if match:
            found.append((int(match.group(1)), item.read_text(encoding="utf-8")))
    return sorted(found)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, autocommit=True)  # transactions are explicit, and executescript keeps them
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        available = migrations()
        latest = available[-1][0] if available else 0
        if _version(connection, path, latest) < latest:
            # Another connection may be migrating too (the page's requests and its update thread): the write lock
            # makes this one wait for it, and the version read again under the lock says what's left to apply.
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = _version(connection, path, latest)
                for number, script in available:
                    if number > current:
                        connection.executescript(f"{script}\nPRAGMA user_version = {number};")
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:  # SQLite ends it itself after some errors
                    connection.execute("ROLLBACK")
                raise
    except BaseException:
        connection.close()
        raise
    return connection


def _version(connection: sqlite3.Connection, path: Path, latest: int) -> int:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > latest:
        raise CodetrailError(f"{path} was written by a newer Codetrail (schema {current}, this one knows {latest}).")
    return current
