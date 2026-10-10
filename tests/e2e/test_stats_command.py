"""`codetrail stats` prints the repository statistics, and writes nothing (design 19.5)."""

from datetime import date
from pathlib import Path

import pytest

from codetrail.cli import main
from codetrail.config import Paths
from codetrail.database import connect
from tests.fixtures.repos import Commit, add_commit, git, make_repository

FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi"]\n',
    "app/main.py": "print('hi')\n",
    "tests/test_main.py": "def test_x():\n    pass\n",
    "README.md": "# Shop\n",
}


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    checkout = make_repository(tmp_path / "target", [FILES])
    git(checkout, "tag", "-a", "v1.0", "-m", "Release one", date=1)
    add_commit(checkout, {"app/main.py": "print('hello')\n"}, "fix: greet")
    assert main(["target", "add", "shop", str(checkout)]) == 0
    return checkout


def recorded_rows() -> int:
    connection = connect(Paths.from_environment().target_data("shop") / "codetrail.db")
    try:
        return int(connection.execute("SELECT COUNT(*) FROM metric_values").fetchone()[0])
    finally:
        connection.close()


def test_before_any_facts_it_says_how_to_start(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stats", "shop"]) == 1
    assert "No facts yet. Run: codetrail update shop --facts-only" in capsys.readouterr().out


def test_it_prints_the_summary_and_writes_nothing(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["update", "shop", "--facts-only"]) == 0
    capsys.readouterr()
    rows = recorded_rows()
    assert main(["stats", "shop"]) == 0
    out = capsys.readouterr().out
    assert "First commit: 2026-09-21" in out
    assert "Commits: 2 (0 merges)" in out
    assert "Releases: 1, the latest v1.0 on 2026-09-21, 1 commit since" in out
    assert "  Python: 2 files, 3 lines" in out
    assert "Documents: 1 file, 1 line" in out
    assert "Tests: 33% (2 of 6 lines)" in out
    assert "Facts: " in out and "package" in out
    assert recorded_rows() == rows


class FixedDate(date):
    @classmethod
    def today(cls) -> FixedDate:
        return cls(2026, 10, 10)


def test_a_young_repository_shows_its_age_in_days(
    checkout: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("codetrail.cli.date", FixedDate)
    assert main(["update", "shop", "--facts-only"]) == 0
    capsys.readouterr()
    assert main(["stats", "shop"]) == 0
    assert "First commit: 2026-09-21 (19 days ago)" in capsys.readouterr().out
