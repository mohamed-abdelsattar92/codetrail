"""A team's catch-up records and overviews (design section 24).

The records live in the team's own database, which uses the targets' migrations. The overviews are Markdown pages with
front matter in their own git repository, written with the guide's page format."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
        failed: Mapping[str, str] = {},
    ) -> None:
        self.connection.execute(
            "UPDATE catch_ups SET finished_at = ?, targets = ?, overview_id = ?, overview_problem = ?, failed = ?"
            " WHERE id = ?",
            (_now(), json.dumps(targets), overview_id, overview_problem, json.dumps(dict(failed)), catch_up_id),
        )

    def last_finished(self) -> CatchUpRecord | None:
        row = self.connection.execute(
            "SELECT * FROM catch_ups WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return CatchUpRecord(
            row["id"],
            row["started_at"],
            row["finished_at"],
            json.loads(row["targets"]),
            row["overview_id"],
            row["overview_problem"],
            json.loads(row["failed"]),
        )

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
