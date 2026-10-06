"""The command line exists and reports its version."""

import signal

import pytest

from codetrail import __version__, cli
from codetrail.cli import main, stop_on_signal


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

    assert version("codetrail") == __version__  # pyproject.toml and __init__.py move together
