"""Pages whose drafts failed validation, remembered so an update doesn't pay for them again unchanged (design 6.5).

A page is remembered with the hash of its scope when it failed; while the hash is the same, updates skip it. A
provider error isn't remembered, since it says nothing about the page.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


class FailedPages:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def scope_hash(self, page_id: str) -> str | None:
        """The scope hash the page failed with, or None when it hasn't failed."""
        row = self.connection.execute("SELECT scope_hash FROM page_failures WHERE page_id = ?", (page_id,)).fetchone()
        return str(row["scope_hash"]) if row else None

    def record(self, page_id: str, scope_hash: str) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        self.connection.execute(
            "INSERT INTO page_failures (page_id, scope_hash, failed_at) VALUES (?, ?, ?) ON CONFLICT (page_id)"
            " DO UPDATE SET scope_hash = excluded.scope_hash, failed_at = excluded.failed_at",
            (page_id, scope_hash, now),
        )

    def forget(self, page_id: str) -> None:
        self.connection.execute("DELETE FROM page_failures WHERE page_id = ?", (page_id,))
