"""`codetrail metrics` prints each documentation metric and its change, and writes nothing (design 18.4)."""

from pathlib import Path

import pytest

from codetrail.cli import main
from codetrail.config import Paths
from codetrail.database import connect
from tests.fixtures.repos import Commit, add_commit, make_repository

FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi"]\n',
    "app/main.py": "print('hi')\n",
    "README.md": "# Shop\n\nBuilt with FastAPI.\n",
    "docs/adr/0001-x.md": "# 0001. X\n\nStatus: proposed\nDate: 2020-01-01\n",
}


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    checkout = make_repository(tmp_path / "target", [FILES])
    add_commit(checkout, {"app/main.py": "print('hello')\n"}, "fix: greet\n\nWhy: friendlier.")
    assert main(["target", "add", "shop", str(checkout)]) == 0
    return checkout


def recorded_rows() -> int:
    connection = connect(Paths.from_environment().target_data("shop") / "codetrail.db")
    try:
        return int(connection.execute("SELECT COUNT(*) FROM metric_values").fetchone()[0])
    finally:
        connection.close()


def test_before_any_facts_it_says_how_to_start(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["metrics", "shop"]) == 1
    assert "No facts yet. Run: codetrail update shop --facts-only" in capsys.readouterr().out


def test_it_prints_every_metric_and_its_change(checkout: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["update", "shop", "--facts-only"]) == 0
    capsys.readouterr()
    assert main(["metrics", "shop"]) == 0
    first = capsys.readouterr().out
    assert "Documented rationale: no pages yet" in first
    assert "Commits that explain why: 50% (1 of 2), no earlier update" in first
    assert "ADRs needing attention: 1, no earlier update" in first
    assert "Mentioned in a document: 100% (2 of 2), no earlier update" in first
    assert "Explained by the guide: no pages yet" in first
    add_commit(checkout, {"app/main.py": "print('hey')\n"}, "perf: faster\n\nWhy: speed.")
    assert main(["update", "shop", "--facts-only"]) == 0
    capsys.readouterr()
    rows = recorded_rows()
    assert main(["metrics", "shop"]) == 0
    second = capsys.readouterr().out
    assert "Commits that explain why: 67% (2 of 3), +17 points since the last update" in second
    assert "ADRs needing attention: 1, +0 since the last update" in second
    assert recorded_rows() == rows  # the command only reads the history
