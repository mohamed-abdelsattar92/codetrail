"""The target's SQLite database and its migrations (design section 4.2)."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from codetrail.database import connect, migrations
from codetrail.errors import CodetrailError


def test_a_new_database_reaches_the_latest_version(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    latest = max(number for number, _ in migrations())
    assert connection.execute("PRAGMA user_version").fetchone()[0] == latest
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"snapshots", "entities", "relations"} <= tables


def test_reopening_applies_nothing(tmp_path: Path) -> None:
    connect(tmp_path / "codetrail.db").close()
    connection = connect(tmp_path / "codetrail.db")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_connection_opened_while_another_migrates_waits_and_applies_nothing(tmp_path: Path) -> None:
    # A page request opening the database while the update creates it (design section 15.4).
    path = tmp_path / "codetrail.db"
    migrating = sqlite3.connect(path, autocommit=True)
    migrating.execute("PRAGMA journal_mode = WAL")
    migrating.execute("BEGIN IMMEDIATE")
    versions: list[int] = []
    failed: list[BaseException] = []

    def open_database() -> None:
        try:
            connection = connect(path)
            versions.append(connection.execute("PRAGMA user_version").fetchone()[0])
            connection.close()
        except BaseException as error:
            failed.append(error)

    opening = threading.Thread(target=open_database)
    opening.start()
    time.sleep(0.5)  # it read the version before the migrations below were committed
    latest = 0
    for number, script in migrations():
        migrating.executescript(f"{script}\nPRAGMA user_version = {number};")
        latest = number
    migrating.execute("COMMIT")
    opening.join()
    assert failed == []
    assert versions == [latest]


def test_a_database_newer_than_the_code_is_refused(tmp_path: Path) -> None:
    raw = sqlite3.connect(tmp_path / "codetrail.db")
    raw.execute("PRAGMA user_version = 9999")
    raw.close()
    with pytest.raises(CodetrailError, match="newer"):
        connect(tmp_path / "codetrail.db")


def test_migrations_are_numbered_without_gaps() -> None:
    numbers = [number for number, _ in migrations()]
    assert numbers == list(range(1, len(numbers) + 1))
