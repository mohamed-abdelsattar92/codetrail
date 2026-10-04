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
    connection = sqlite3.connect(path, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    available = migrations()
    latest = available[-1][0] if available else 0
    if current > latest:
        connection.close()
        raise CodetrailError(f"{path} was written by a newer Codetrail (schema {current}, this one knows {latest}).")
    for number, script in available:
        if number > current:
            connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
    return connection
