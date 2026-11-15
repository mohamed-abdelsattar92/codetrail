"""The team's catch-up records, overviews folder and database (design section 24)."""

from datetime import date
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
    assert record.unticked == []
    assert record.started_at <= record.finished_at


def test_team_state_records_why_targets_failed(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    failed = {"acme-api": "Target 'acme-api' is busy with its own update.", "acme-shop": "The update failed (X)."}

    state.finish(state.start(), ["acme-api", "acme-shop"], None, None, failed)

    record = state.last_finished()
    assert record is not None and record.failed == failed


def test_team_state_records_the_unticked_targets(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))

    state.finish(state.start(), ["acme-api"], None, None, unticked=["acme-shop", "acme-docs"])

    record = state.last_finished()
    assert record is not None and record.unticked == ["acme-shop", "acme-docs"] and record.failed == {}


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


def finished_at(
    state: TeamState, monkeypatch: pytest.MonkeyPatch, started: str, finished: str, targets: list[str] | None = None
) -> int:
    monkeypatch.setattr("codetrail.team.state._now", lambda: started)
    catch_up_id = state.start()
    monkeypatch.setattr("codetrail.team.state._now", lambda: finished)
    state.finish(catch_up_id, ["acme-api"] if targets is None else targets, None, None)
    return catch_up_id


def test_a_finished_catch_up_records_its_digests(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    digests = [("acme-web-shop", "digests/2026-11-11-abc123")]

    state.finish(state.start(), ["acme-web-shop"], None, None, digests=digests)
    recorded = state.last_finished()
    state.finish(state.start(), ["acme-web-shop"], None, None)
    unrecorded = state.last_finished()

    assert recorded is not None and recorded.digests == digests
    assert unrecorded is not None and unrecorded.digests is None


def test_finished_between_lists_ticked_catch_ups_by_day_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    finished_at(state, monkeypatch, "2026-11-10T23:50:00+00:00", "2026-11-10T23:59:59+00:00")
    midnight = finished_at(state, monkeypatch, "2026-11-10T23:58:00+00:00", "2026-11-11T00:00:00+00:00")
    morning = finished_at(state, monkeypatch, "2026-11-12T07:00:00+00:00", "2026-11-12T08:00:00+00:00")
    after = finished_at(state, monkeypatch, "2026-11-13T00:00:00+00:00", "2026-11-13T00:00:01+00:00")

    found = state.finished_between(date(2026, 11, 11), date(2026, 11, 12))

    assert [record.id for record in found] == [morning, midnight]
    assert after not in [record.id for record in found]


def test_finished_between_leaves_out_declined_and_corrupt_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    finished_at(state, monkeypatch, "2026-11-11T09:00:00+00:00", "2026-11-11T09:01:00+00:00", targets=[])
    bad_digests = finished_at(state, monkeypatch, "2026-11-11T10:00:00+00:00", "2026-11-11T10:01:00+00:00")
    bad_targets = finished_at(state, monkeypatch, "2026-11-11T11:00:00+00:00", "2026-11-11T11:01:00+00:00")
    good = finished_at(state, monkeypatch, "2026-11-11T12:00:00+00:00", "2026-11-11T12:01:00+00:00")
    state.connection.execute("UPDATE catch_ups SET digests = '{\"x\": 1}' WHERE id = ?", (bad_digests,))
    state.connection.execute("UPDATE catch_ups SET targets = 'not json' WHERE id = ?", (bad_targets,))

    found = state.finished_between(date(2026, 11, 11), date(2026, 11, 11))

    assert [record.id for record in found] == [good]


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("failed", "[]"),
        ("failed", '{"acme-api": 3}'),
        ("unticked", '{"a": "b"}'),
        ("unticked", "[1]"),
        pytest.param("unticked", "[" * 100_000 + "]" * 100_000, id="deeply-nested"),
    ],
)
def test_finished_between_leaves_out_rows_with_a_wrong_shaped_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: str, value: str
) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    bad = finished_at(state, monkeypatch, "2026-11-11T10:00:00+00:00", "2026-11-11T10:01:00+00:00")
    good = finished_at(state, monkeypatch, "2026-11-11T12:00:00+00:00", "2026-11-11T12:01:00+00:00")
    update = {
        "failed": "UPDATE catch_ups SET failed = ? WHERE id = ?",
        "unticked": "UPDATE catch_ups SET unticked = ? WHERE id = ?",
    }[column]
    state.connection.execute(update, (value, bad))

    assert [record.id for record in state.finished_between(date(2026, 11, 11), date(2026, 11, 11))] == [good]


def test_a_hand_edited_empty_target_list_counts_as_none_in_both_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    spaced = finished_at(state, monkeypatch, "2026-11-11T10:00:00+00:00", "2026-11-11T10:01:00+00:00")
    state.connection.execute("UPDATE catch_ups SET targets = '[ ]' WHERE id = ?", (spaced,))

    assert state.finished_between(date(2026, 11, 11), date(2026, 11, 11)) == []
    assert not state.has_caught_up()


def test_has_caught_up_needs_a_finished_catch_up_with_a_ticked_target(tmp_path: Path) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    assert not state.has_caught_up()
    state.finish(state.start(), [], None, None)  # declined
    state.start()  # still running
    corrupt = state.start()
    state.finish(corrupt, ["shop"], None, None)
    state.connection.execute("UPDATE catch_ups SET failed = '[]' WHERE id = ?", (corrupt,))
    assert not state.has_caught_up()

    state.finish(state.start(), ["shop"], None, None)

    assert state.has_caught_up()


def test_last_finished_is_none_when_the_newest_row_is_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    finished_at(state, monkeypatch, "2026-11-11T10:00:00+00:00", "2026-11-11T10:01:00+00:00")
    newest = finished_at(state, monkeypatch, "2026-11-11T12:00:00+00:00", "2026-11-11T12:01:00+00:00")
    state.connection.execute("UPDATE catch_ups SET targets = 'not json' WHERE id = ?", (newest,))

    assert state.last_finished() is None


@pytest.mark.parametrize("bad_time", ["2026-11-11garbage", "2026-11-11T10:00:00", "2026-11-11T12:00:00+02:00"])
@pytest.mark.parametrize("column", ["started_at", "finished_at"])
def test_a_row_whose_time_is_not_utc_is_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, column: str, bad_time: str
) -> None:
    state = TeamState(connect(tmp_path / "team.db"))
    bad = finished_at(state, monkeypatch, "2026-11-11T10:00:00+00:00", "2026-11-11T10:01:00+00:00")
    good = finished_at(state, monkeypatch, "2026-11-11T12:00:00+00:00", "2026-11-11T12:01:00+00:00")
    update = {
        "started_at": "UPDATE catch_ups SET started_at = ? WHERE id = ?",
        "finished_at": "UPDATE catch_ups SET finished_at = ? WHERE id = ?",
    }[column]
    state.connection.execute(update, (bad_time, bad))
    first, last = date(2026, 11, 11), date(2026, 11, 12)

    assert [record.id for record in state.finished_between(first, last)] == [good]
    if column == "finished_at":
        assert state.last_finished() is not None and state.last_finished().id == good  # type: ignore[union-attr]
