"""The command line exists and reports its version."""

import pytest

from codetrail import __version__
from codetrail.cli import main


def test_version_prints_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err
