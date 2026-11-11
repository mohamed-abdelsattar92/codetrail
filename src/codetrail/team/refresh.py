"""The free first refresh of the repositories a team just added: clone, scan and facts (design section 24.6).

Nothing paid runs: each new target's first guide waits for the next catch-up's estimate.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from codetrail.config import Paths, load_target
from codetrail.errors import CodetrailError
from codetrail.update import run_update

logger = logging.getLogger(__name__)


class TeamRefreshJob:
    """Refreshes the facts of a team's new targets in a background thread, `concurrency` at a time, one run at a time,
    keeping each target's steps (`started`, then `done` or `failed`) for the add-repositories screen.

    `stop()`, when the server stops, lets no further target start and waits for the ones running, as `UpdateJob`
    waits for its update's cleanup."""

    def __init__(
        self, paths: Paths, concurrency: int, gettext: Callable[[str], str] = str, log_lines: int = 200
    ) -> None:
        self._paths = paths
        self._concurrency = concurrency
        self.gettext = gettext  # the screen's catalog, for the one message that isn't Codetrail's own error
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._log: deque[tuple[int, dict[str, object]]] = deque(maxlen=log_lines)  # (number, step), newest last
        self._numbered = 0  # keeps counting across runs, so a screen asking for the steps after one misses nothing
        self._pending: set[str] = set()  # the run's targets not done or failed yet
        self.state = "idle"

    @property
    def running(self) -> bool:
        return self.state == "running"

    def is_pending(self, target: str) -> bool:
        """Whether the target waits for its refresh or is being refreshed."""
        with self._lock:
            return target in self._pending

    def start(self, targets: list[str]) -> bool:
        """Starts refreshing the targets; False while a run is going or once the server is stopping."""
        with self._lock:  # stop() sets _stopping under it, so it joins every thread started here
            if self._stopping.is_set() or self.running:
                return False
            self.state = "running"
            self._log.clear()
            self._pending = set(targets)
            self._thread = threading.Thread(target=self._work, args=(list(targets),), daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            self._stopping.set()
            thread = self._thread
        if thread is not None:
            thread.join()

    def _work(self, targets: list[str]) -> None:
        try:
            with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
                list(pool.map(self._refresh, targets))
        finally:
            self.state = "done"

    def _refresh(self, target: str) -> None:
        try:
            self._refresh_one(target)
        finally:
            with self._lock:
                self._pending.discard(target)

    def _refresh_one(self, target: str) -> None:
        if self._stopping.is_set():
            return
        named = {"target": target, "repository": self._repository_name(target)}  # the screen shows the repository's
        self._report({"step": "started", **named})
        try:
            run_update(self._paths, target, facts_only=True)
        except CodetrailError as error:  # its message, as the page's update shows it (it may quote git's)
            self._report({"step": "failed", **named, "message": str(error)})
        except Exception as error:  # one repository mustn't stop the others; details stay out of the screen
            logger.warning("Couldn't refresh target %r: %s", target, type(error).__name__)
            message = self.gettext("Couldn't refresh this repository (%(error)s).") % {"error": type(error).__name__}
            self._report({"step": "failed", **named, "message": message})
        else:
            self._report({"step": "done", **named})

    def _repository_name(self, target: str) -> str:
        """The name the service gives the target's repository, or the target's own if its settings can't be read."""
        try:
            remote = load_target(self._paths, target).remote
        except CodetrailError:
            return target
        return remote.name if remote is not None else target

    def _report(self, step: dict[str, object]) -> None:
        with self._lock:
            self._numbered += 1
            self._log.append((self._numbered, step))

    def status(self, after: int) -> dict[str, object]:
        """The state, the steps numbered after `after`, oldest first, and the last step's number."""
        with self._lock:
            steps = [step for number, step in self._log if number > after]
            return {"state": self.state, "steps": steps, "next": self._numbered}
