"""`codetrail agents`: rebuild the context for coding agents and print how to connect them (design section 23.4)."""

import sys
from pathlib import Path

import pytest

from codetrail.cli import main
from tests.fixtures.repos import make_repository, snapshot_tree

PYPROJECT = '[project]\nname = "api"\ndependencies = ["fastapi==0.120.0"]\n'


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    return tmp_path


def add(environment: Path) -> Path:
    checkout = make_repository(environment / "target", [{"services/api/pyproject.toml": PYPROJECT}])
    assert main(["target", "add", "api", str(checkout)]) == 0
    return checkout


def test_agents_prints_the_setup_and_changes_nothing(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checkout = add(environment)
    assert main(["update", "api", "--facts-only"]) == 0
    before = snapshot_tree(checkout)
    (environment / "data" / "codetrail" / "api" / "agents" / "map.md").unlink()
    capsys.readouterr()
    assert main(["agents", "api"]) == 0
    out = capsys.readouterr().out
    assert sys.executable in out and "-I -m codetrail mcp api" in out and "--scope user" in out
    assert "committed" in out
    assert snapshot_tree(checkout) == before
    assert (environment / "data" / "codetrail" / "api" / "agents" / "map.md").exists()  # rebuilt


def test_agents_before_any_update_says_what_to_run(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add(environment)
    capsys.readouterr()
    assert main(["agents", "api"]) == 1
    assert "Run `codetrail update api` first" in capsys.readouterr().err
