"""The command line exists and reports its version."""

import signal
from collections.abc import Collection
from pathlib import Path
from typing import Any

import pytest

from codetrail import __version__, cli
from codetrail.assistant.estimate import CallEstimate, EstimateLine, UpdateEstimate
from codetrail.cli import main, stop_on_signal
from codetrail.config import Paths, RemoteSettings, TeamSource, write_remote_target
from codetrail.lock import team_catch_up_lock
from codetrail.team.catch_up import CatchUpEstimate, CatchUpLine, CatchUpResult
from codetrail.team.settings import save_selection, write_team
from codetrail.team.sync import TeamSync


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


@pytest.mark.parametrize(("arguments", "open_browser"), [([], True), (["--no-browser"], False)])
def test_team_add_writes_the_team_and_opens_its_add_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], open_browser: bool
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    served: list[tuple[str, bool, str]] = []

    def serve(paths: Paths, name: str, open_browser: bool, start_path: str = "/") -> None:
        assert paths.team_file(name).is_file()  # written before it's served
        served.append((name, open_browser, start_path))

    monkeypatch.setattr("codetrail.cli.serve_team", serve)

    assert main(["team", "add", "acme", *arguments]) == 0

    assert served == [("acme", open_browser, "/team/add")]
    assert main(["team", "add", "acme"]) == 1
    assert main(["team", "add", "Not A Team"]) == 1
    assert len(served) == 1


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


@pytest.mark.parametrize(("arguments", "open_browser"), [([], True), (["--no-browser"], False)])
def test_team_serve_serves_the_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], open_browser: bool
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    served: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "codetrail.cli.serve_team", lambda paths, name, open_browser: served.append((name, open_browser))
    )

    assert main(["team", "serve", "acme", *arguments]) == 0

    assert served == [("acme", open_browser)]


def fake_estimate() -> CatchUpEstimate:
    call = CallEstimate("write", "claude_code", "claude-sonnet-5-5", 1000, 0, 0.5, False)
    update = UpdateEstimate([EstimateLine(call, 1, 1)], {"claude_code": "Claude subscription"}, [], 5.0, 1_000_000)
    lines = [
        CatchUpLine("acme-repo-a", "repo-a", update, True, False, False),
        CatchUpLine("acme-repo-b", "repo-b", update, False, False, False),
    ]
    return CatchUpEstimate(lines, None, [("repo-c", "refused")], 20.0, {}, [])


def fake_catch_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answers: list[Collection[str] | None]) -> None:
    """Replaces `run_catch_up` with one that sends two steps, asks once and records the answer."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    write_team(Paths.from_environment(), "acme")
    monkeypatch.setattr("codetrail.cli.sync_team", lambda *arguments: TeamSync())

    def run(paths: Paths, name: str, confirm: Any, progress: Any, stop: Any, sync: Any) -> CatchUpResult:
        sync()
        progress({"step": "write", "repository": "repo-a", "target": "acme-repo-a"})
        answers.append(confirm(fake_estimate()))
        failed = [("repo-b", "the refresh failed")]
        return CatchUpResult(fake_estimate(), [], [], failed, None, answers[-1] is None)

    monkeypatch.setattr("codetrail.cli.run_catch_up", run)


def test_team_update_asks_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers: list[Collection[str] | None] = []
    fake_catch_up(monkeypatch, tmp_path, answers)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts: list[str] = []

    def decline(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", decline)

    assert main(["team", "update", "acme"]) == 0

    assert answers == [None]
    output = capsys.readouterr().out
    assert prompts == ["Continue? [y/N] "]
    assert "repo-a (first guide): write" in output
    assert "repo-b: write" in output


def test_team_update_yes_ticks_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers: list[Collection[str] | None] = []
    fake_catch_up(monkeypatch, tmp_path, answers)

    assert main(["team", "update", "acme", "--yes"]) == 0

    assert answers == [["acme-repo-a", "acme-repo-b"]]  # the first guide too
    assert "Continue?" not in capsys.readouterr().out


def test_team_update_without_a_terminal_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers: list[Collection[str] | None] = []
    fake_catch_up(monkeypatch, tmp_path, answers)

    assert main(["team", "update", "acme"]) == 2

    assert answers == [None]
    assert "Run again with --yes" in capsys.readouterr().out


def test_team_update_prints_steps_with_names_and_the_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_catch_up(monkeypatch, tmp_path, [])

    assert main(["team", "update", "acme", "--yes"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("repo-a: write") for line in lines)
    assert "repo-b: not updated (the refresh failed)" in lines
    assert "No overview was written." in lines


def test_team_update_busy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    paths = Paths.from_environment()
    write_team(paths, "acme")
    monkeypatch.setattr("codetrail.cli.sync_team", lambda *arguments: TeamSync())

    with team_catch_up_lock(paths, "acme"):
        assert main(["team", "update", "acme", "--yes"]) == 1

    assert "already catching up" in capsys.readouterr().err
