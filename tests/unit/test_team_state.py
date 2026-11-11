"""The team's catch-up records, overviews folder and database (design section 24)."""

from pathlib import Path

import pytest

from codetrail.config import Paths
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.team.state import OVERVIEW_ID, Overviews, TeamState, team_database


def test_team_database_is_in_the_teams_data_folder(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "c", data_dir=tmp_path / "d", state_dir=tmp_path / "s")
    assert team_database(paths, "acme") == paths.team_data("acme") / "team.db"


def test_team_state_round_trip(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    catch_up_id = state.start()
    state.finish(catch_up_id, ["shop", "api"], "overviews/2026-11-11-101500", None)
    record = state.last_finished()
    assert record is not None
    assert record.id == catch_up_id
    assert record.targets == ["shop", "api"]
    assert record.overview_id == "overviews/2026-11-11-101500"
    assert record.overview_problem is None
    assert record.failed == {}
    assert record.started_at <= record.finished_at


def test_team_state_records_why_targets_failed(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    failed = {"acme-api": "Target 'acme-api' is busy with its own update.", "acme-shop": "The update failed (X)."}

    state.finish(state.start(), ["acme-api", "acme-shop"], None, None, failed)

    record = state.last_finished()
    assert record is not None and record.failed == failed


def test_last_finished_ignores_unfinished(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    assert state.last_finished() is None
    state.start()
    assert state.last_finished() is None


def test_last_finished_is_the_latest(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    state.finish(state.start(), ["a"], None, "no budget")
    second = state.start()
    state.finish(second, ["b"], None, None)
    record = state.last_finished()
    assert record is not None
    assert record.id == second


def test_overview_read(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    assert not state.overview_read("overviews/2026-11-11-101500")
    state.mark_overview_read("overviews/2026-11-11-101500")
    state.mark_overview_read("overviews/2026-11-11-101500")
    assert state.overview_read("overviews/2026-11-11-101500")
    assert not state.overview_read("overviews/2026-11-12-101500")


def test_overviews_write_read_latest(tmp_path: Path) -> None:
    overviews = Overviews(tmp_path / "overviews")
    assert overviews.latest() is None
    overviews.write("overviews/2026-11-11-101500", {"targets": ["shop"]}, "First.")
    overviews.write("overviews/2026-11-12-090000", {"targets": ["shop", "api"]}, "Second.")
    assert overviews.latest() == "overviews/2026-11-12-090000"
    assert overviews.read("overviews/2026-11-12-090000") == ({"targets": ["shop", "api"]}, "Second.")
    assert overviews.read("overviews/2026-11-11-101500") == ({"targets": ["shop"]}, "First.")
    assert overviews.read("overviews/2026-01-01-000000") is None
    assert (tmp_path / "overviews" / ".git").is_dir()


def test_overviews_refuse_ids_outside_the_folder(tmp_path: Path) -> None:
    overviews = Overviews(tmp_path / "overviews")
    assert overviews.read("../x") is None
    assert overviews.read("overviews/../../x") is None
    with pytest.raises(CodetrailError, match="valid overview id"):
        overviews.write("../x", {}, "No.")


def test_overview_ids_are_ascii_digits_only(tmp_path: Path) -> None:
    # The same id in Arabic-Indic digits: an id from a POST body never matches with other digits.
    arabic_indic = "overviews/2026-11-11-101500".translate({ord(digit): 0x0660 + int(digit) for digit in "0123456789"})

    assert OVERVIEW_ID.fullmatch(arabic_indic) is None
    assert OVERVIEW_ID.fullmatch("overviews/2026-11-11-101500") is not None
    with pytest.raises(CodetrailError, match="valid overview id"):
        Overviews(tmp_path / "overviews").write(arabic_indic, {}, "No.")
