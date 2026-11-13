"""Shared test settings."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

REAL_PROGRAMS = {"claude", "codex", "gh", "az", "glab"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def private_codetrail_folders(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codetrail's configuration, data and state folders (and the gitleaks scan folders under the state one) point at
    a temporary folder per test, so no test writes into the real home folder. A test or fixture that sets one of these
    variables itself runs later, so its value wins."""
    folder = tmp_path_factory.mktemp("xdg")
    for variable, name in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")):
        monkeypatch.setenv(variable, str(folder / name))


@pytest.fixture(autouse=True)
def no_real_providers(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Only tests marked `live` may run a real provider or hosting program (claude, codex, gh, az, glab).

    Elsewhere it fails at once, costing nothing.
    """
    if request.node.get_closest_marker("live") is None:
        from codetrail.assistant import runner

        real_program, real_command = runner.run_program, runner.run_command

        def refuse(command: Any) -> None:
            if Path(str(command[0])).name in REAL_PROGRAMS:
                raise AssertionError("A test tried to run a real program; use a fake or mark it live.")

        async def program(command: Any, *arguments: Any, **keywords: Any) -> AsyncIterator[str]:
            refuse(command)
            async for line in real_program(command, *arguments, **keywords):
                yield line

        def short(command: Any, *arguments: Any, **keywords: Any) -> tuple[int, str]:
            refuse(command)
            return real_command(command, *arguments, **keywords)

        monkeypatch.setattr(runner, "run_program", program)
        monkeypatch.setattr(runner, "run_command", short)
    yield
