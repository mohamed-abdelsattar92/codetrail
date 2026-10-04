"""`codetrail target add` and `codetrail files` end to end, on a hostile repository that must stay untouched."""

from pathlib import Path

import pytest

from codetrail.cli import main
from tests.fixtures.repos import Symlink, add_commit, fake_github_token, make_repository, snapshot_tree


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    return tmp_path


@pytest.fixture
def hostile(tmp_path: Path) -> Path:
    return make_repository(
        tmp_path / "target",
        [
            {
                "README.md": "# Target\n",
                "app/main.py": "print('hi')\n",
                "app/settings.py": f'TOKEN = "{fake_github_token()}"\n',
                ".env": "PASSWORD=not-a-real-one\n",
                ".env.example": "PASSWORD=\n",
                "infra/prod.tfvars": 'region = "x"\n',
                "keys/server.pem": "not a real key\n",
                "docs/private/notes.md": "private\n",
                "docs/design/logo.png": b"\x89PNG fake",
                ".codetrailignore": "docs/private/\n",
                "escape": Symlink("/etc/passwd"),
            }
        ],
    )


def test_target_add_then_files(environment: Path, hostile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = snapshot_tree(hostile)
    assert main(["target", "add", "hostile", str(hostile), "--branch", "develop"]) == 0
    ignore = environment / "config" / "codetrail" / "targets" / "hostile.ignore"
    ignore.write_text("**/*.png\n")
    capsys.readouterr()

    assert main(["files", "hostile"]) == 0
    output = capsys.readouterr().out
    visible = output.split("Excluded:")[0].split()
    assert visible == [".codetrailignore", ".env.example", "README.md", "app/main.py"]
    assert "secret pattern\t.env\n" in output
    assert "secret pattern\tinfra/prod.tfvars\n" in output
    assert "secret pattern\tkeys/server.pem\n" in output
    assert "ignore rules\tdocs/private/notes.md\n" in output
    assert "ignore rules\tdocs/design/logo.png\n" in output
    assert "gitleaks (github-pat)\tapp/settings.py\n" in output
    assert "symlink or submodule\tescape\n" in output
    assert "4 visible, 7 excluded" in output
    assert fake_github_token() not in output

    add_commit(hostile, {"app/extra.py": "y = 2\n"})
    after_commit = snapshot_tree(hostile)
    assert main(["files", "hostile"]) == 0
    assert "app/extra.py" in capsys.readouterr().out
    assert snapshot_tree(hostile) == after_commit
    assert set(before) <= set(after_commit)


def test_errors_are_reported_without_a_traceback(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["files", "missing"]) == 1
    assert "codetrail: No target named 'missing'" in capsys.readouterr().err


def test_target_add_refuses_a_folder_that_isnt_a_repository(
    environment: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "plain").mkdir()
    assert main(["target", "add", "plain", str(tmp_path / "plain")]) == 1
    assert "isn't a git repository" in capsys.readouterr().err


def test_target_add_refuses_a_missing_branch(
    environment: Path, hostile: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["target", "add", "hostile", str(hostile), "--branch", "main"]) == 1
    assert "no branch 'main'" in capsys.readouterr().err
