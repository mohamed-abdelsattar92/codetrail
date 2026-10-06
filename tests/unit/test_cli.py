"""The command line exists and reports its version."""

import signal

import pytest

from codetrail import __version__
from codetrail.cli import main, stop_on_signal


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_the_stop_signal_handler_fires_once() -> None:
    # Closing a terminal can send a second hangup; it mustn't interrupt the cleanup the first one started.
    before = {number: signal.getsignal(number) for number in (signal.SIGHUP, signal.SIGTERM)}
    try:
        with pytest.raises(SystemExit) as exit_info:
            stop_on_signal(signal.SIGHUP, None)
        assert exit_info.value.code == 128 + signal.SIGHUP
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
    finally:
        for number, handler in before.items():
            signal.signal(number, handler)
