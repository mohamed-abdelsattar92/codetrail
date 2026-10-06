"""An update started from the page stops its paid work when the server stops (design section 15.5)."""

import threading
from typing import Any

import anyio
import pytest

from codetrail import update
from codetrail.errors import CodetrailError
from codetrail.generate.run import GenerationResult

UNUSED: Any = None  # the context and assistant, which the stand-in generations ignore


def test_stopping_cancels_the_generation_so_its_cleanup_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    cleaned: list[bool] = []
    stop = threading.Event()

    async def generation_that_waits(_context: Any, _claude: Any) -> GenerationResult:
        try:
            stop.set()
            await anyio.sleep(60)
        finally:
            cleaned.append(True)
        raise AssertionError("not cancelled")

    monkeypatch.setattr(update, "generate_guide", generation_that_waits)
    with pytest.raises(CodetrailError, match="server stopped"):
        anyio.run(update.generate_until_stopped, UNUSED, UNUSED, stop)
    assert cleaned == [True]


def test_the_generations_own_error_comes_through_as_it_is(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_generation(_context: Any, _claude: Any) -> GenerationResult:
        raise CodetrailError("The budget is spent.")

    monkeypatch.setattr(update, "generate_guide", failing_generation)
    with pytest.raises(CodetrailError, match="budget"):
        anyio.run(update.generate_until_stopped, UNUSED, UNUSED, threading.Event())


def test_an_unstopped_generation_returns_its_result(monkeypatch: pytest.MonkeyPatch) -> None:
    result = GenerationResult()

    async def generation(_context: Any, _claude: Any) -> GenerationResult:
        return result

    monkeypatch.setattr(update, "generate_guide", generation)
    assert anyio.run(update.generate_until_stopped, UNUSED, UNUSED, threading.Event()) is result


def test_no_update_starts_once_the_server_is_stopping() -> None:
    from codetrail.web.app import UpdateJob

    runs: list[bool] = []
    job = UpdateJob(lambda _confirm: runs.append(True), threading.Event())
    job.stop()
    assert job.start() == "The server is stopping."
    assert runs == []
