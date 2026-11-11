"""The team page's catch-up (design section 24.8): one at a time, in a background thread, as the guide page's
`UpdateJob` runs one target's update.

The free part runs first. Then the one estimate waits until the reader confirms it with its single-use id and the
ticked targets, cancels it, or `estimate_ttl_seconds` pass; only a confirmation lets paid work start (design 15.4).
A confirmation that names a target the estimate doesn't hold, or one target twice, is refused and the estimate keeps
waiting. Stopping the server declines a waiting estimate, stops the paid parts and waits for every target's cleanup.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Collection

from codetrail.errors import CodetrailError
from codetrail.generate.run import Progress
from codetrail.team.catch_up import CatchUpEstimate
from codetrail.web.app import wait_for_cleanup

logger = logging.getLogger(__name__)
Confirm = Callable[[CatchUpEstimate], Collection[str] | None]


class CatchUpJob:
    """`run(confirm, progress, stop)` runs one catch-up; `stop` is the job's own event, set when the server stops."""

    def __init__(
        self,
        run: Callable[[Confirm, Progress, threading.Event], object],
        cooldown_seconds: int,
        estimate_ttl_seconds: int,
        log_lines: int,
        gettext_of: Callable[[], Callable[[str], str]],
    ) -> None:
        self._run = run
        self._gettext_of = gettext_of  # the reader's catalog, for the messages the page shows (design section 7.2)
        self.gettext: Callable[[str], str] = str
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._cooldown = cooldown_seconds
        self._ttl = estimate_ttl_seconds
        self._log: deque[tuple[int, dict[str, object]]] = deque(maxlen=log_lines)  # (number, step), newest last
        self._numbered = 0  # keeps counting across catch-ups, so a page asking for the steps after one misses nothing
        self._finished_at: float | None = None
        self._decided = threading.Event()
        self._ticked: list[str] | None = None  # the confirmed targets; None cancels
        self._lines: set[str] = set()  # the waiting estimate's targets
        self._declined = False
        self.state = "idle"
        self.message = ""
        self.estimate: dict[str, object] | None = None
        self.estimate_id: str | None = None

    def _confirm(self, estimate: CatchUpEstimate) -> Collection[str] | None:
        with self._lock:
            if self._stopping.is_set():  # the server stopped during the free part: nothing is asked
                self._declined = True
                return None
            self._decided.clear()
            self._ticked = None
            self._lines = {line.target for line in estimate.lines}
            self.estimate, self.estimate_id = estimate.as_json(), secrets.token_urlsafe(16)
            self.state = "waiting"
        self._decided.wait(self._ttl)
        with self._lock:
            # Decided under the lock: a confirmation racing the expiry either counts, or is refused by decide().
            answered = self._decided.is_set() and not self._stopping.is_set()
            self.estimate, self.estimate_id = None, None
            if not self._decided.is_set():
                self.message = self.gettext("The estimate expired before it was confirmed, so nothing was spent.")
            self.state = "running"
            ticked = self._ticked if answered else None
            self._declined = ticked is None
            return ticked

    def decide(self, estimate_id: str, targets: list[str] | None) -> bool:
        """Confirms the waiting estimate for `targets`, or cancels it (None). The id works once, only while it
        waits, and only with targets from its lines, each named once; a refusal leaves the estimate waiting."""
        with self._lock:
            expected = self.estimate_id
            if (
                self._stopping.is_set()
                or self.state != "waiting"
                or expected is None
                or not hmac.compare_digest(estimate_id.encode(), expected.encode())
            ):
                return False
            if targets is not None and (len(set(targets)) != len(targets) or not set(targets) <= self._lines):
                return False
            self.estimate_id = None
            self._ticked = targets
            self._decided.set()
            return True

    def start(self) -> str | None:
        """Starts the catch-up; returns why it can't (one running, or the last one finished too recently)."""
        _ = self._reader_gettext()  # outside the lock: it reads the reader's language
        with self._lock:  # stop() sets _stopping under it, so it joins every thread started here
            if self._stopping.is_set():
                return _("The server is stopping.")
            if self.state in ("preparing", "waiting", "running"):
                return _("A catch-up is already running.")
            if self._finished_at is not None and time.monotonic() - self._finished_at < self._cooldown:
                return _("A catch-up finished a moment ago; wait a few minutes before the next.")
            self.gettext = _  # the catch-up's own messages use the language chosen when it started
            self.state, self.message, self._declined = "preparing", "", False
            self._log.clear()
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()
        return None

    def _reader_gettext(self) -> Callable[[str], str]:
        """The reader's catalog, or English if it can't be read: a message must never fail the handlers."""
        try:
            return self._gettext_of()
        except Exception:
            return str

    def request_stop(self) -> None:
        """Declines a waiting estimate and lets no catch-up start; the running one's paid parts stop."""
        with self._lock:  # under the lock, so _confirm either sees it before waiting or is woken by it
            self._stopping.set()
            self._decided.set()

    def wait_for_stop(self) -> None:
        """Waits for every target's cleanup once `request_stop()` ran (see `wait_for_cleanup`)."""
        if self._thread is not None:
            wait_for_cleanup(self._thread, "Stopping the catch-up started from the page; waiting for its cleanup.")

    def _work(self) -> None:
        try:
            result = self._run(self._confirm, self._report, self._stopping)
        except CodetrailError as error:
            self.state, self.message = "failed", str(error)
        except Exception as error:  # the page shows a generic message; details stay in the log
            logger.warning("The catch-up failed: %s", type(error).__name__, exc_info=True)
            self.state, self.message = (
                "failed",
                self.gettext("The catch-up failed (%(error)s).") % {"error": type(error).__name__},
            )
        else:
            if self._declined or getattr(result, "declined", False):
                reason = self.message or self.gettext("The catch-up was cancelled.")
                declined = self.gettext("%(reason)s The facts were refreshed; no guide was updated.")
                self.state, self.message = "declined", declined % {"reason": reason}
            else:
                self.state, self.message = "done", ""
        finally:
            self._finished_at = time.monotonic()

    def _report(self, step: dict[str, object]) -> None:
        with self._lock:
            self._numbered += 1
            self._log.append((self._numbered, step))

    def status(self, after: int) -> dict[str, object]:
        """The state, the steps numbered after `after`, oldest first, the last step's number, and, while it waits,
        the estimate and its id."""
        with self._lock:
            status: dict[str, object] = {
                "state": self.state,
                "message": self.message,
                "steps": [step for number, step in self._log if number > after],
                "next": self._numbered,
            }
            if self.state == "waiting" and self.estimate is not None:
                status |= {"message": "", "estimate": self.estimate, "estimate_id": self.estimate_id}
            return status
