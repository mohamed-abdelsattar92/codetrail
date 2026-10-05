"""Learning state: what the reader has read and learned, and what went stale (design section 8.3).

A page's version is a hash of its body and checks. Passing a check records its hash, so when an update rewrites a
page, checks that didn't change stay passed and only new or changed ones need answering again. A page is learned when
every current check has a pass; a page learned before whose version changed is stale until it is learned again.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from codetrail.guide import Page

PASS = "pass"  # noqa: S105 - a verdict, not a password
VERDICTS = ("pass", "partial", "fail")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_hash(check: Mapping[str, Any]) -> str:
    return _hash({"question": check.get("question"), "rubric": check.get("rubric")})


def page_version(page: Page) -> str:
    return _hash({"body": page.body, "checks": page.meta.get("checks") or []})


def page_checks(page: Page) -> list[dict[str, Any]]:
    return [check for check in page.meta.get("checks") or [] if isinstance(check, dict) and check.get("id")]


@dataclass(frozen=True)
class PageStatus:
    state: str  # "unread", "read", "learned" or "stale"
    passed: set[str] = field(default_factory=set)
    learned_commit: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LearningState:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def mark_read(self, page: Page) -> None:
        self.connection.execute(
            "INSERT INTO page_marks (page_id, read_version, read_at) VALUES (?, ?, ?)"
            " ON CONFLICT (page_id) DO UPDATE SET read_version = excluded.read_version, read_at = excluded.read_at",
            (page.id, page_version(page), _now()),
        )

    def mark_unread(self, page: Page) -> None:
        """Takes back a read mark; a learned page stays learned, since that came from its checks."""
        self.connection.execute(
            "UPDATE page_marks SET read_version = NULL, read_at = NULL WHERE page_id = ?", (page.id,)
        )

    def record_attempt(
        self,
        page: Page,
        check: Mapping[str, Any],
        answer: str,
        verdict: str,
        feedback: str,
        language: str,
        guide_commit: str | None,
    ) -> PageStatus:
        """Stores an attempt; a pass is recorded for the check's hash, and a page whose checks all pass is learned."""
        digest = check_hash(check)
        self.connection.execute(
            "INSERT INTO check_attempts (page_id, check_id, check_hash, answer, verdict, feedback, language,"
            " attempted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (page.id, str(check["id"]), digest, answer, verdict, feedback, language, _now()),
        )
        if verdict == PASS:
            self.connection.execute(
                "INSERT OR IGNORE INTO check_passes (page_id, check_id, check_hash, passed_at) VALUES (?, ?, ?, ?)",
                (page.id, str(check["id"]), digest, _now()),
            )
        if self._all_passed(page):
            self.connection.execute(
                "INSERT INTO page_marks (page_id, learned_version, learned_commit, learned_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (page_id) DO UPDATE SET learned_version = excluded.learned_version,"
                " learned_commit = excluded.learned_commit, learned_at = excluded.learned_at",
                (page.id, page_version(page), guide_commit, _now()),
            )
        return self.status(page)

    def _passed(self, page: Page) -> set[str]:
        passes = {
            (row["check_id"], row["check_hash"])
            for row in self.connection.execute(
                "SELECT check_id, check_hash FROM check_passes WHERE page_id = ?", (page.id,)
            )
        }
        return {str(check["id"]) for check in page_checks(page) if (str(check["id"]), check_hash(check)) in passes}

    def _all_passed(self, page: Page) -> bool:
        checks = page_checks(page)
        return bool(checks) and self._passed(page) == {str(check["id"]) for check in checks}

    def status(self, page: Page) -> PageStatus:
        row = self.connection.execute("SELECT * FROM page_marks WHERE page_id = ?", (page.id,)).fetchone()
        passed = self._passed(page)
        learned_commit = row["learned_commit"] if row else None
        if row and row["learned_version"]:
            if row["learned_version"] == page_version(page) or self._all_passed(page):
                return PageStatus("learned", passed, learned_commit)
            return PageStatus("stale", passed, learned_commit)
        if row and row["read_version"]:
            return PageStatus("read", passed)
        return PageStatus("unread", passed)

    def learned_page_ids(self) -> set[str]:
        return {
            row["page_id"]
            for row in self.connection.execute("SELECT page_id FROM page_marks WHERE learned_version IS NOT NULL")
        }

    def mark_digest_read(self, digest_id: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO digest_reads (digest_id, read_at) VALUES (?, ?)", (digest_id, _now())
        )

    def unread_digests(self, digest_ids: Iterable[str]) -> list[str]:
        read = {row["digest_id"] for row in self.connection.execute("SELECT digest_id FROM digest_reads")}
        return [digest for digest in digest_ids if digest not in read]

    def attempts(self, page_id: str) -> Sequence[sqlite3.Row]:
        return self.connection.execute(
            "SELECT check_id, verdict, feedback, attempted_at FROM check_attempts WHERE page_id = ? ORDER BY id",
            (page_id,),
        ).fetchall()
