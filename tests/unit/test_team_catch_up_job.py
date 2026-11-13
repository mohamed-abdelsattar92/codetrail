"""The catch-up job's state, without a server (design section 24.8)."""

import threading
import time

import pytest

from codetrail.team import catch_up_job
from codetrail.team.catch_up_job import CatchUpJob


def test_a_catch_up_that_just_finished_is_never_seen_without_its_finish_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # A start() between the state turning "done" and the finish time being set passed the cooldown.
    job = CatchUpJob(lambda confirm, progress, stop: None, 300, 60, 10, lambda: str)
    real_monotonic = time.monotonic
    answers: list[str | None] = []
    starter: list[threading.Thread] = []

    def monotonic() -> float:
        if threading.current_thread() is job._thread and not starter:  # the job noting when it finished
            starter.append(threading.Thread(target=lambda: answers.append(job.start())))
            starter[0].start()
            starter[0].join(0.5)  # under the lock, the start waits until the finish time is set
        return real_monotonic()

    monkeypatch.setattr(catch_up_job.time, "monotonic", monotonic)
    assert job.start() is None
    assert job._thread is not None
    job._thread.join(5)
    starter[0].join(5)

    assert answers == ["A catch-up finished a moment ago; wait a few minutes before the next."]
    assert job.state == "done"
