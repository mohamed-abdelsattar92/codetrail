"""An update started from the page stops its paid work when the server stops (design section 15.5)."""

import os
import signal
import threading
import time
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


def test_no_generation_starts_once_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    # A confirmation that raced the stop: not one program may start.
    started: list[bool] = []
    stop = threading.Event()
    stop.set()

    async def generation(_context: Any, _claude: Any) -> GenerationResult:
        started.append(True)
        return GenerationResult()

    monkeypatch.setattr(update, "generate_guide", generation)
    with pytest.raises(CodetrailError, match="server stopped"):
        anyio.run(update.generate_until_stopped, UNUSED, UNUSED, stop)
    assert started == []


def test_a_confirmation_once_the_server_is_stopping_is_refused() -> None:
    from codetrail.web.app import UpdateJob
    from tests.api.test_update_estimate import ESTIMATE

    decisions: list[bool] = []
    stopping = threading.Event()
    job = UpdateJob(lambda confirm: decisions.append(confirm(ESTIMATE)), stopping)
    job.start()
    while job.estimate_id is None:
        time.sleep(0.01)
    estimate_id = job.estimate_id
    stopping.set()  # stop() has begun, and this confirmation reached the lock first
    assert job.decide(estimate_id, go_ahead=True) is False
    job.stop()
    assert decisions == [False]


class Interrupted(Exception):
    pass


def test_a_signal_during_the_stop_still_waits_for_the_updates_cleanup() -> None:
    from codetrail.web.app import UpdateJob

    cleaned: list[bool] = []

    def update_with_slow_cleanup(_confirm: Any) -> None:
        time.sleep(0.5)
        cleaned.append(True)

    def interrupt(_number: int, _frame: Any) -> None:
        raise Interrupted

    job = UpdateJob(update_with_slow_cleanup, threading.Event())
    job.start()
    previous = signal.signal(signal.SIGUSR1, interrupt)
    try:
        threading.Timer(0.1, os.kill, (os.getpid(), signal.SIGUSR1)).start()
        with pytest.raises(Interrupted):  # the signal still ends the server, once the cleanup ran
            job.stop()
    finally:
        signal.signal(signal.SIGUSR1, previous)
    assert cleaned == [True]


def test_a_signal_while_the_stop_reports_its_wait_still_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    from codetrail.web import app
    from codetrail.web.app import UpdateJob

    cleaned: list[bool] = []

    def update_with_slow_cleanup(_confirm: Any) -> None:
        time.sleep(0.3)
        cleaned.append(True)

    def interrupted_warning(*_arguments: Any) -> None:
        raise Interrupted  # the terminal closing while the line is written

    monkeypatch.setattr(app.logger, "warning", interrupted_warning)
    job = UpdateJob(update_with_slow_cleanup, threading.Event())
    job.start()
    with pytest.raises(Interrupted):
        job.stop()
    assert cleaned == [True]
