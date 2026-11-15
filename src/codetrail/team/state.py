"""A team's catch-up records and overviews (design section 24).

The records live in the team's own database, which uses the targets' migrations. The overviews are Markdown pages with
front matter in their own git repository, written with the guide's page format."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from codetrail.config import Paths
from codetrail.errors import CodetrailError
from codetrail.guide import GuideRepository, Page, parse_page, render_page

OVERVIEW_ID = re.compile(r"overviews/[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}")  # ASCII digits only: ids come from POSTs


def team_database(paths: Paths, name: str) -> Path:
    return paths.team_data(name) / "team.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CatchUpRecord:
    id: int
    started_at: str
    finished_at: str
    targets: list[str]
    overview_id: str | None
    overview_problem: str | None
    failed: dict[str, str]  # why each target that failed wasn't caught up, by target
    unticked: list[str]  # the targets whose estimate line the reader left unticked
    digests: list[tuple[str, str]] | None  # (target, digest id) for each digest written; None before design 25


def _record(row: sqlite3.Row) -> CatchUpRecord | None:
    """The record a row holds, or None when a JSON column doesn't parse or isn't the shape it should be."""
    try:
        targets, failed, unticked = json.loads(row["targets"]), json.loads(row["failed"]), json.loads(row["unticked"])
        pairs = None if row["digests"] is None else json.loads(row["digests"])
        if not (_strings(targets) and _strings(unticked) and isinstance(failed, dict)):
            return None
        if not all(isinstance(key, str) and isinstance(reason, str) for key, reason in failed.items()):
            return None
        digests = None
        if pairs is not None:
            if not (
                isinstance(pairs, list)
                and all(isinstance(pair, list) and len(pair) == 2 and _strings(pair) for pair in pairs)
            ):
                return None
            digests = [(pair[0], pair[1]) for pair in pairs]
    except json.JSONDecodeError, TypeError:
        return None
    return CatchUpRecord(row["id"], row["started_at"], row["finished_at"], targets, row["overview_id"],
                         row["overview_problem"], failed, unticked, digests)  # fmt: skip


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


class TeamState:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def start(self) -> int:
        cursor = self.connection.execute("INSERT INTO catch_ups (started_at, targets) VALUES (?, '[]')", (_now(),))
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def finish(
        self,
        catch_up_id: int,
        targets: list[str],
        overview_id: str | None,
        overview_problem: str | None,
        failed: Mapping[str, str] | None = None,
        unticked: Sequence[str] | None = None,
        digests: Sequence[tuple[str, str]] | None = None,
    ) -> None:
        self.connection.execute(
            "UPDATE catch_ups SET finished_at = ?, targets = ?, overview_id = ?, overview_problem = ?, failed = ?,"
            " unticked = ?, digests = ? WHERE id = ?",
            (
                _now(),
                json.dumps(targets),
                overview_id,
                overview_problem,
                json.dumps(dict(failed or {})),
                json.dumps(list(unticked or [])),
                None if digests is None else json.dumps([list(pair) for pair in digests]),
                catch_up_id,
            ),
        )

    def last_finished(self) -> CatchUpRecord | None:
        row = self.connection.execute(
            "SELECT * FROM catch_ups WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _record(row)

    def finished_between(self, first: date, last: date) -> list[CatchUpRecord]:
        """The finished catch-ups with a ticked target whose UTC day lies in `first..last`, newest first. A row
        whose columns hold JSON of the wrong shape is left out."""
        rows = self.connection.execute(
            "SELECT * FROM catch_ups WHERE finished_at >= ? AND finished_at < ? AND targets != '[]'"
            " ORDER BY finished_at DESC, id DESC",
            (first.isoformat(), (last + timedelta(days=1)).isoformat()),
        ).fetchall()
        return [record for record in map(_record, rows) if record is not None]

    def mark_overview_read(self, overview_id: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO overview_reads (overview_id, read_at) VALUES (?, ?)", (overview_id, _now())
        )

    def overview_read(self, overview_id: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM overview_reads WHERE overview_id = ?", (overview_id,)).fetchone()
        return row is not None


class Overviews:
    """The team's overviews: `overviews/<date>-<time>.md` files in a git repository of their own. Its root is the
    team's `overviews` folder, so a file is at `<team data>/overviews/overviews/<id>.md`: the id carries the inner
    folder, as a guide's page ids carry theirs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repository = GuideRepository(root)

    def _path_of(self, overview_id: str) -> Path:
        if not OVERVIEW_ID.fullmatch(overview_id):
            raise CodetrailError(f"{overview_id!r} isn't a valid overview id.")
        return self.root / f"{overview_id}.md"

    def write(self, overview_id: str, meta: dict[str, object], body: str) -> None:
        path = self._path_of(overview_id)
        self.repository.ensure()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_page(Page(overview_id, meta, body)), encoding="utf-8")
        self.repository.commit(f"Overview {overview_id.removeprefix('overviews/')}")

    def read(self, overview_id: str) -> tuple[dict[str, object], str] | None:
        try:
            path = self._path_of(overview_id)
        except CodetrailError:
            return None
        if not path.is_file():
            return None
        page = parse_page(overview_id, path.read_text(encoding="utf-8"))
        return page.meta, page.body

    def latest(self) -> str | None:
        folder = self.root / "overviews"
        if not folder.is_dir():
            return None
        ids = [f"overviews/{path.stem}" for path in folder.glob("*.md")]
        valid = [overview_id for overview_id in ids if OVERVIEW_ID.fullmatch(overview_id)]
        return max(valid, default=None)
