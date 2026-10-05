"""`codetrail target remove` end to end: it asks first, deletes Codetrail's copy, and never touches the target."""

from pathlib import Path

import pytest

from codetrail.cli import main
from codetrail.config import Paths
from codetrail.errors import CodetrailError
from codetrail.lock import target_removal
from codetrail.server import serve
from tests.fixtures.repos import make_repository, snapshot_tree


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for variable, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(tmp_path / folder))
    return tmp_path


@pytest.fixture
def checkout(environment: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    checkout = make_repository(environment / "target", [{"README.md": "x\n", "app/main.py": "print(1)\n"}])
    assert main(["target", "add", "api", str(checkout)]) == 0
    assert main(["update", "api", "--facts-only"]) == 0
    capsys.readouterr()
    return checkout


def test_remove_with_yes_deletes_everything_and_leaves_the_target_alone(
    environment: Path, checkout: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = snapshot_tree(checkout)

    assert main(["target", "remove", "api", "--yes"]) == 0

    output = capsys.readouterr().out
    assert str(environment / "data" / "codetrail" / "api") in output
    assert str(environment / "config" / "codetrail" / "targets" / "api.toml") in output
    assert "Removed target 'api'." in output
    assert not (environment / "data" / "codetrail" / "api").exists()
    assert not (environment / "config" / "codetrail" / "targets" / "api.toml").exists()
    assert snapshot_tree(checkout) == before
    assert main(["update", "api", "--facts-only"]) == 1


def test_remove_asks_and_honours_no(
    environment: Path, checkout: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    questions: list[str] = []

    def answer_no(question: str) -> str:
        questions.append(question)
        return "n"

    monkeypatch.setattr("builtins.input", answer_no)

    assert main(["target", "remove", "api"]) == 0

    assert questions == ["Remove target 'api' and delete these? [y/N] "]
    assert "Nothing was removed." in capsys.readouterr().out
    assert (environment / "config" / "codetrail" / "targets" / "api.toml").exists()


def test_remove_asks_and_honours_yes(
    environment: Path, checkout: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda question: "y")

    assert main(["target", "remove", "api"]) == 0

    assert "Removed target 'api'." in capsys.readouterr().out
    assert not (environment / "data" / "codetrail" / "api").exists()


def test_remove_without_a_terminal_needs_yes(
    environment: Path, checkout: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert main(["target", "remove", "api"]) == 2

    assert "--yes" in capsys.readouterr().out
    assert (environment / "data" / "codetrail" / "api").exists()


def test_removing_an_unknown_target_fails(environment: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["target", "remove", "api", "--yes"]) == 1
    assert "No target named 'api'" in capsys.readouterr().err


@pytest.mark.parametrize("command", [["update", "api", "--facts-only"], ["files", "api"]])
def test_a_target_being_removed_is_not_refreshed(
    environment: Path, checkout: Path, capsys: pytest.CaptureFixture[str], command: list[str]
) -> None:
    with target_removal(Paths.from_environment(), "api"):
        assert main(command) == 1
    assert "being removed" in capsys.readouterr().err


def test_a_target_being_removed_is_not_served(
    environment: Path, checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("codetrail.server.uvicorn.run", lambda *arguments, **keywords: None)
    paths = Paths.from_environment()
    with target_removal(paths, "api"), pytest.raises(CodetrailError, match="being removed"):
        serve(paths, "api", open_browser=False)


def test_a_served_target_is_not_removed(
    environment: Path, checkout: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exits: list[int] = []
    monkeypatch.setattr(
        "codetrail.server.uvicorn.run",
        lambda *arguments, **keywords: exits.append(main(["target", "remove", "api", "--yes"])),
    )

    assert main(["serve", "api", "--no-browser"]) == 0

    assert exits == [1]
    assert "in use: stop `codetrail serve` for it" in capsys.readouterr().err
    assert (environment / "data" / "codetrail" / "api").exists()
