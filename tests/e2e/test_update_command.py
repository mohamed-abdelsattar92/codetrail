"""`codetrail update` end to end: facts recorded with validity ranges, and the target never touched."""

from pathlib import Path

import pytest

from codetrail.cli import main
from tests.fixtures.repos import add_commit, make_repository, snapshot_tree

PYPROJECT = '[project]\nname = "api"\ndependencies = ["fastapi==0.120.0"]\n'


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    return tmp_path


def test_update_records_and_reports_changes(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checkout = make_repository(
        environment / "target",
        [
            {
                "services/api/pyproject.toml": PYPROJECT,
                "services/api/app/__init__.py": "",
                "services/api/app/main.py": "from fastapi import FastAPI\nfrom app import db\n",
                "services/api/app/db.py": "",
                "docs/adr/0001-use-fastapi.md": "# 0001. Use FastAPI\n\n- Status: accepted\n- Date: 2026-01-01\n",
            }
        ],
    )
    assert main(["target", "add", "api", str(checkout)]) == 0
    capsys.readouterr()

    assert main(["update", "api"]) == 0
    first = capsys.readouterr().out
    assert "Facts: 6 entities" in first
    assert "decision: +1" in first
    assert "module: +3" in first
    assert (environment / "data" / "codetrail" / "api" / "codetrail.db").exists()

    add_commit(
        checkout,
        {
            "services/api/app/orders.py": "from app.db import x\n",
            "services/api/pyproject.toml": PYPROJECT.replace("0.120.0", "0.121.0"),
        },
    )
    before = snapshot_tree(checkout)
    assert main(["update", "api"]) == 0
    second = capsys.readouterr().out
    assert "module: +1" in second
    assert "depends_on: +0 ~1 -0" in second
    assert snapshot_tree(checkout) == before

    assert main(["update", "api"]) == 0
    assert "No changes since the last update." in capsys.readouterr().out


def test_unknown_extractors_are_refused(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    checkout = make_repository(environment / "target", [{"README.md": "x\n"}])
    assert main(["target", "add", "api", str(checkout)]) == 0
    file = environment / "config" / "codetrail" / "targets" / "api.toml"
    file.write_text(file.read_text() + 'extractors = ["python", "cobol"]\n')
    capsys.readouterr()
    assert main(["update", "api"]) == 1
    assert "extractors" in capsys.readouterr().err


def test_nothing_is_created_when_the_data_folder_would_sit_inside_the_target(
    environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checkout = make_repository(environment / "target", [{"README.md": "x\n"}])
    assert main(["target", "add", "api", str(checkout)]) == 0
    file = environment / "config" / "codetrail" / "targets" / "api.toml"
    file.write_text(file.read_text().replace(str(checkout), str(environment)))  # the repository now holds data/
    before = sorted(path.name for path in (environment / "data").glob("*")) if (environment / "data").exists() else []
    assert main(["update", "api"]) == 1
    assert "inside" in capsys.readouterr().err
    after = sorted(path.name for path in (environment / "data").glob("*")) if (environment / "data").exists() else []
    assert after == before
