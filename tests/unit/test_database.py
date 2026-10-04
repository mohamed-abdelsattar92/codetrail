"""The target's SQLite database and its migrations (design section 4.2)."""

import sqlite3
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


def test_a_database_newer_than_the_code_is_refused(tmp_path: Path) -> None:
    raw = sqlite3.connect(tmp_path / "codetrail.db")
    raw.execute("PRAGMA user_version = 9999")
    raw.close()
    with pytest.raises(CodetrailError, match="newer"):
        connect(tmp_path / "codetrail.db")


def test_migrations_are_numbered_without_gaps() -> None:
    numbers = [number for number, _ in migrations()]
    assert numbers == list(range(1, len(numbers) + 1))
