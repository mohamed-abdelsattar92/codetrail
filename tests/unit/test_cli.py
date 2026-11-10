"""The command line exists and reports its version."""

import signal
from pathlib import Path

import pytest

from codetrail import __version__, cli
from codetrail.cli import main, stop_on_signal
from codetrail.config import Paths, RemoteSettings, TeamSource, write_remote_target
from codetrail.team.settings import save_selection, write_team


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_the_stop_signal_handler_fires_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Closing a terminal can send a second hangup; it mustn't interrupt the cleanup the first one started. The signals
    # stay caught, not ignored, so a program started during the cleanup gets the default handling back.
    monkeypatch.setattr(cli, "stopping", False)
    with pytest.raises(SystemExit) as exit_info:
        stop_on_signal(signal.SIGHUP, None)
    assert exit_info.value.code == 128 + signal.SIGHUP
    stop_on_signal(signal.SIGHUP, None)
    stop_on_signal(signal.SIGTERM, None)


def test_the_installed_version_is_the_one_the_code_reports() -> None:
    from importlib.metadata import version

    assert version("codetrail-cli") == __version__  # pyproject.toml and __init__.py move together


def test_team_add_writes_the_team_and_names_the_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert main(["team", "add", "acme"]) == 0

    assert (tmp_path / "config" / "codetrail" / "teams" / "acme.toml").is_file()
    assert "codetrail team serve acme" in capsys.readouterr().out
    assert main(["team", "add", "acme"]) == 1
    assert main(["team", "add", "Not A Team"]) == 1


def test_team_list_prints_sources_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    paths = Paths.from_environment()
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
    save_selection(paths, "acme", TeamSource(service="gitlab", source="example-group", follow="chosen", chosen=["1"]))
    remote = RemoteSettings(
        service="github", source="acme-org", id="a1", name="web-shop", url="https://github.com/acme-org/web-shop.git"
    )
    write_remote_target(paths, "acme-web-shop", remote, "main", "acme")
    write_team(paths, "empty")
    capsys.readouterr()

    assert main(["team", "list"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "acme: github:acme-org (all), gitlab:example-group (chosen) - 1 target"
    assert lines[1] == "empty: no sources - 0 targets"


def test_team_remove_asks_and_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    write_team(Paths.from_environment(), "acme")

    assert main(["team", "remove", "acme"]) == 2  # no terminal to ask in
    assert Paths.from_environment().team_file("acme").exists()
    assert main(["team", "remove", "acme", "--yes"]) == 0

    assert not Paths.from_environment().team_file("acme").exists()
    assert "Removed team 'acme'" in capsys.readouterr().out
