"""A team's catch-up (design section 24.8): every followed repository's update in a thread of its own, one question
for all of them, the ticked ones' paid parts under one budget the team shares, then the overview of their digests.

Each target's update runs as it does alone, with its own lock, budget and all-or-nothing commit. Its thread holds a
free slot through the free part; its `confirm` gives that slot back, reports the target's line and waits for the
reader's one decision, and a ticked target then holds a paid slot until its update returns. A thread that fails before
its `confirm` reports too, so the question never waits for it, and every thread is released and joined on every way
out of the catch-up, the reader's decision, an error or the server stopping.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass

import anyio

from codetrail.assistant import Assistant, OverviewDigest
from codetrail.assistant.estimate import (
    CallEstimate,
    EstimateLine,
    PlanUsageReading,
    UpdateEstimate,
    account_text,
    call_text,
    estimate_call,
    plan_usage,
    tokens_text,
)
from codetrail.assistant.routing import build_overview_assistant
from codetrail.assistant.status import provider_status, require_provider_ready
from codetrail.assistant.usage import UsageLog
from codetrail.config import GlobalConfig, Paths, TeamConfig, load_global, load_team, model_choice
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.generate.run import TEAM_BUDGET_SPENT, Progress, SharedBudget, no_progress
from codetrail.guide import GuideRepository
from codetrail.lock import team_catch_up_lock
from codetrail.repo.secrets import SecretScanner
from codetrail.team.follow import followed_targets
from codetrail.team.overview import OverviewOutcome, write_overview
from codetrail.team.state import Overviews, TeamState, team_database
from codetrail.team.sync import TeamSync
from codetrail.update import STOPPED, UpdateResult, run_update, until_stopped

FAILED_UNEXPECTEDLY = "The update failed ({error})."  # an error that isn't Codetrail's own: only its type is shown
OVERVIEW_STOPPED = "The server stopped, so the overview wasn't written."
OVERVIEW_FAILED = "The overview couldn't be written ({error})."  # an error that isn't Codetrail's own
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatchUpLine:
    """One repository's paid work in the catch-up's estimate, which the reader can untick."""

    target: str
    repository: str  # the name the service gives it
    estimate: UpdateEstimate
    first_guide: bool  # its guide had no pages before this catch-up
    digest_expected: bool  # its estimate expects a digest
    digest_possible: bool  # it may write one: a digest also runs when commits came in and no page was written


@dataclass(frozen=True)
class Totals:
    expected_tokens: int
    maximum_tokens: int
    expected_usd: float | None  # None when a call has no configured price
    maximum_usd: float | None


@dataclass(frozen=True)
class CatchUpEstimate:
    """What the whole catch-up's paid calls are expected to use, and at most, before any starts (design 24.8)."""

    lines: list[CatchUpLine]
    overview: CallEstimate | None  # None when fewer than two lines may write a digest
    failed: list[tuple[str, str]]  # the repository and why, for each target whose free part failed
    budget_usd: float  # catch_up.max_budget_usd
    sign_ins: dict[str, str]
    plan_usage: list[PlanUsageReading]

    def _sums(self, ticked: Collection[str]) -> Totals:
        """The ticked lines' totals. The overview counts in the maximums when at least two of them may write a
        digest, and in the expected figures when at least two expect one. The dollars at most are capped at the
        team's budget, where the catch-up stops starting calls, and nothing expected exceeds its maximum."""
        lines = [line for line in self.lines if line.target in ticked]
        estimates = [line.estimate for line in lines]
        overview = self.overview
        at_most = [overview] if overview is not None and sum(line.digest_possible for line in lines) >= 2 else []
        expected = [overview] if overview is not None and sum(line.digest_expected for line in lines) >= 2 else []
        maximum_tokens = sum(estimate.maximum_tokens for estimate in estimates) + sum(call.tokens for call in at_most)
        maximum_usd = _total([estimate.maximum_usd for estimate in estimates] + [call.cost_usd for call in at_most])
        if maximum_usd is not None:
            maximum_usd = min(maximum_usd, self.budget_usd)
        expected_usd = _total([estimate.expected_usd for estimate in estimates] + [call.cost_usd for call in expected])
        if expected_usd is not None and maximum_usd is not None:
            expected_usd = min(expected_usd, maximum_usd)
        expected_tokens = sum(estimate.expected_tokens for estimate in estimates)
        expected_tokens += sum(call.tokens for call in expected)
        return Totals(min(expected_tokens, maximum_tokens), maximum_tokens, expected_usd, maximum_usd)

    def totals(self, ticked: Collection[str]) -> dict[str, float | int | None]:
        return asdict(self._sums(ticked))

    def as_json(self) -> dict[str, object]:
        overview = self.overview
        return {
            "lines": [{"target": line.target, "repository": line.repository, "first_guide": line.first_guide,
                       "digest_expected": line.digest_expected, "digest_possible": line.digest_possible,
                       "estimate": line.estimate.as_json()}
                      for line in self.lines],
            "overview": None if overview is None else {
                "kind": overview.kind, "provider": overview.provider, "model": overview.model,
                "tokens_each": overview.tokens, "cost_each_usd": overview.cost_usd,
                "from_history": overview.from_history},
            "failed": [{"repository": repository, "reason": reason} for repository, reason in self.failed],
            "budget_usd": self.budget_usd,
            "sign_ins": self.sign_ins,
            "plan_usage": [{"provider": reading.provider, "window": reading.window,
                            "utilization": reading.utilization, "resets_at": reading.resets_at,
                            "observed_at": reading.observed_at} for reading in self.plan_usage],
            "totals": self.totals([line.target for line in self.lines]),
        }  # fmt: skip


def _total(amounts: list[float | None]) -> float | None:
    known = [amount for amount in amounts if amount is not None]
    return sum(known) if len(known) == len(amounts) else None


def describe_catch_up(estimate: CatchUpEstimate) -> list[str]:
    """The estimate as lines of text, for the command line, every line ticked."""
    lines = ["This catch-up will call:"]
    for line in estimate.lines:
        repository = line.repository + (" (first guide)" if line.first_guide else "")
        lines += [f"  {repository}: {call_text(call)}" for call in line.estimate.lines]
    if estimate.overview is not None:
        lines.append(f"  The team: {call_text(EstimateLine(estimate.overview, 1, 1))}, once two repositories have "
                     "new digests")  # fmt: skip
    totals = estimate._sums([line.target for line in estimate.lines])
    expected, maximum = totals.expected_usd, totals.maximum_usd
    money = "" if expected is None or maximum is None else f", ~${expected:.2f} (at most ${maximum:.2f})"
    lines.append(f"Expected: ~{tokens_text(totals.expected_tokens)} tokens (at most "
                 f"~{tokens_text(totals.maximum_tokens)}){money} at API prices. The catch-up stops starting calls "
                 f"at the team's budget of ${estimate.budget_usd:.2f}.")  # fmt: skip
    if estimate.failed:
        lines.append("These repositories won't be caught up, since their refresh failed:")
        lines += [f"  {repository}: {reason}" for repository, reason in estimate.failed]
    return lines + account_text(estimate.sign_ins, estimate.plan_usage)


@dataclass(frozen=True)
class CatchUpResult:
    estimate: CatchUpEstimate | None  # None when nothing paid was due, so nothing was asked
    ticked: list[str]
    updated: list[str]  # the targets whose update ran to its end, paid part included
    failed: list[tuple[str, str]]  # the repository and why, for each target whose update failed
    overview: OverviewOutcome | None  # None when fewer than two digests were written
    declined: bool


@dataclass
class _Target:
    """One target's thread: what it reported, and how its update ended. Its fields are written by its own thread
    (`reported`, `line` and `free_failure` under the coordinator's condition) and read by the coordinator once the
    thread has reported or ended."""

    name: str
    repository: str
    reported: bool = False
    line: CatchUpLine | None = None  # None when its update has nothing to pay for
    free_failure: str | None = None  # why it failed before reporting its estimate
    result: UpdateResult | None = None
    failure: str | None = None


class _Coordinator:
    """The threads' shared state: the slots, the reports and the reader's decision."""

    def __init__(
        self,
        paths: Paths,
        concurrency: int,
        budget: SharedBudget,
        progress: Progress,
        stop: threading.Event,
        writer_for: Callable[[str], Assistant | None],
    ) -> None:
        self.paths = paths
        self.budget = budget
        self.stop = stop
        self.writer_for = writer_for
        self.free_slots = threading.Semaphore(concurrency)
        self.paid_slots = threading.Semaphore(concurrency)
        self.changed = threading.Condition()  # guards the reports and the decision
        self.decided = False
        self.ticked: set[str] = set()
        self._progress = progress
        self._progress_lock = threading.Lock()  # one step at a time reaches the caller, whichever thread reports it

    def step(self, event: dict[str, object]) -> None:
        with self._progress_lock:
            self._progress(event)

    def report(self, target: _Target, line: CatchUpLine | None, free_failure: str | None) -> None:
        """Counts the target as reported, once: its line, or why it failed before it had one."""
        with self.changed:
            if target.reported:
                return
            target.reported, target.line, target.free_failure = True, line, free_failure
            self.changed.notify_all()

    def wait_for_reports(self, targets: list[_Target]) -> None:
        with self.changed:
            self.changed.wait_for(lambda: all(target.reported for target in targets))

    def decide(self, ticked: set[str]) -> None:
        """Releases every waiting thread: the ticked targets go on to their paid parts, the others decline."""
        with self.changed:
            self.ticked, self.decided = ticked, True
            self.changed.notify_all()

    def is_ticked(self, target: str) -> bool:
        with self.changed:
            self.changed.wait_for(lambda: self.decided)
            return target in self.ticked

    def run(self, target: _Target) -> None:
        """A target's thread: its update, with the slots and the report released on every way out."""
        holds_free_slot = holds_paid_slot = False

        def progress(event: dict[str, object]) -> None:
            self.step({**event, "repository": target.repository, "target": target.name})

        def confirm(estimate: UpdateEstimate) -> bool:
            nonlocal holds_free_slot, holds_paid_slot
            self.free_slots.release()
            holds_free_slot = False
            first_guide = not GuideRepository(self.paths.target_data(target.name) / "guide").has_guide_pages()
            digest = [line for line in estimate.lines if line.call.kind == "digest"]
            line = CatchUpLine(target.name, target.repository, estimate, first_guide,
                               any(line.expected for line in digest), any(line.maximum for line in digest))  # fmt: skip
            self.report(target, line if estimate.lines else None, None)
            if not estimate.lines:  # nothing paid: nothing to ask
                return True
            if not self.is_ticked(target.name):
                return False
            self.paid_slots.acquire()
            holds_paid_slot = True
            if self.budget.exhausted:  # the targets before it spent the team's budget: nothing is committed
                target.failure = TEAM_BUDGET_SPENT
                return False
            return True

        self.free_slots.acquire()
        holds_free_slot = True
        try:
            if self.stop.is_set():  # the server stopped while it waited for its slot
                raise CodetrailError(STOPPED)
            target.result = run_update(self.paths, target.name, claude=self.writer_for(target.name), confirm=confirm,
                                       progress=progress, stop=self.stop, shared_budget=self.budget)  # fmt: skip
        except CodetrailError as error:  # its messages are Codetrail's own
            target.failure = str(error)
        except Exception as error:  # one repository mustn't stop the others; details stay in the log
            logger.warning("Target %r's update failed: %s", target.name, type(error).__name__, exc_info=True)
            target.failure = FAILED_UNEXPECTEDLY.format(error=type(error).__name__)
        finally:
            if holds_free_slot:
                self.free_slots.release()
            if holds_paid_slot:
                self.paid_slots.release()
            self.report(target, None, target.failure)  # a target that failed before its `confirm` reports here
            if target.failure is not None:  # the panel names each failure, and why
                progress({"step": "target_failed", "reason": target.failure})


def _ticked(answer: Collection[str] | None, estimate: CatchUpEstimate, stop: threading.Event) -> set[str] | None:
    """The reader's ticked targets; None declines every line: no answer, the server stopping, no target ticked, or an
    answer that names a target that isn't in the estimate, or one twice."""
    if answer is None or stop.is_set():
        return None
    chosen = list(answer)
    listed = {line.target for line in estimate.lines}
    if not chosen or len(set(chosen)) != len(chosen) or not set(chosen) <= listed:
        return None
    return set(chosen)


def _overview_confirmed(estimate: CatchUpEstimate | None, ticked: set[str] | None) -> bool:
    """Whether the reader confirmed an estimate that showed the overview: its line, and two ticked lines that may
    write a digest. Only such an overview runs (design sections 15.4 and 24.9)."""
    if estimate is None or estimate.overview is None or ticked is None:
        return False
    return sum(line.digest_possible for line in estimate.lines if line.target in ticked) >= 2


def _join(threads: list[threading.Thread], stop: threading.Event) -> None:
    """Waits for every thread. Interrupted (Ctrl-C at the command line), it stops the paid parts and waits for their
    cleanup before raising."""
    try:
        for thread in threads:
            thread.join()
    except BaseException:
        stop.set()
        for thread in threads:
            thread.join()
        raise


def _latest_readings(readings: list[PlanUsageReading]) -> list[PlanUsageReading]:
    latest: dict[tuple[str, str], PlanUsageReading] = {}
    for reading in readings:
        key = (reading.provider, reading.window)
        if key not in latest or reading.observed_at > latest[key].observed_at:
            latest[key] = reading
    return [latest[key] for key in sorted(latest)]


def _digests(paths: Paths, targets: list[_Target]) -> list[OverviewDigest]:
    """The digests this catch-up's updates wrote, with their repositories' names, for the overview."""
    digests = []
    for target in targets:
        generation = target.result.generation if target.result is not None else None
        if generation is None or generation.digest is None:
            continue
        page = GuideRepository(paths.target_data(target.name) / "guide").read_page(generation.digest)
        if page is not None:
            digests.append(OverviewDigest(target.name, target.repository, generation.digest, page.title, page.body,
                                          page.meta.get("highlights", [])))  # fmt: skip
    return digests


def run_catch_up(
    paths: Paths,
    name: str,
    confirm: Callable[[CatchUpEstimate], Collection[str] | None],
    progress: Progress = no_progress,
    stop: threading.Event | None = None,
    sync: Callable[[], TeamSync] = TeamSync,
    writer_for: Callable[[str], Assistant | None] = lambda target: None,
    overview_writer: Assistant | None = None,
) -> CatchUpResult:
    """Catches the team up, under its catch-up lock (design section 24.8).

    `sync` lists the sources again and adds new targets; the targets it reports stopped are left out. `confirm` is
    asked once, with every target's paid line, and returns the ticked targets or None. `writer_for(target)` and
    `overview_writer` are the assistants (tests' fakes), or None for the real ones. `stop` set by the time `confirm`
    returns declines every line, and set later it stops the paid parts and the overview; an interrupted wait for the
    threads (Ctrl-C) sets it too."""
    stop = stop or threading.Event()
    with team_catch_up_lock(paths, name):
        stopped = set(sync().stopped)
        team = load_team(paths, name)
        settings = load_global(paths)
        targets = [_Target(target, repository) for target, (repository, _branch)
                   in followed_targets(paths, name, team).items() if target not in stopped]  # fmt: skip
        connection = connect(team_database(paths, name))
        try:
            state = TeamState(connection)
            catch_up_id = state.start()
            budget = SharedBudget(team.catch_up.max_budget_usd)
            coordinator = _Coordinator(paths, team.catch_up.concurrency, budget, progress, stop, writer_for)
            coordinator.step({"step": "catch_up_started"})
            threads: list[threading.Thread] = []
            estimate: CatchUpEstimate | None = None
            ticked: set[str] | None = None
            try:
                for target in targets:
                    thread = threading.Thread(target=coordinator.run, args=(target,), name=f"catch-up {target.name}")
                    thread.start()
                    threads.append(thread)
                coordinator.wait_for_reports(targets)
                estimate = _estimate(targets, team, settings, connection, overview_writer)
                if estimate is not None and not stop.is_set():
                    ticked = _ticked(confirm(estimate), estimate, stop)
            finally:  # whatever happened, the waiting threads are released and every thread ends
                coordinator.decide(ticked or set())
                _join(threads, stop)
            overview = None
            digests = _digests(paths, targets)
            if _overview_confirmed(estimate, ticked) and len(digests) >= 2:
                coordinator.step({"step": "overview"})
                overview = _overview(paths, name, team, settings, connection, digests, budget, stop, overview_writer)
            chosen = [line.target for line in estimate.lines if line.target in ticked] if estimate and ticked else []
            # Each card says why its repository wasn't caught up: its failure, or the reader leaving it out.
            reasons = {target.name: target.failure for target in targets if target.failure is not None}
            unticked = [line.target for line in estimate.lines if line.target not in chosen] if estimate else []
            state.finish(catch_up_id, chosen, overview.overview_id if overview else None,
                         overview.problem if overview else None, reasons, unticked)  # fmt: skip
        finally:
            connection.close()
    updated = [target.name for target in targets if target.result is not None and not target.result.declined]
    failed = [(target.repository, target.failure) for target in targets if target.failure is not None]
    coordinator.step({"step": "catch_up_done", "updated": len(updated), "failed": len(failed)})
    return CatchUpResult(estimate, chosen, updated, failed, overview, estimate is not None and ticked is None)


def _estimate(
    targets: list[_Target],
    team: TeamConfig,
    settings: GlobalConfig,
    connection: sqlite3.Connection,
    overview_writer: Assistant | None,
) -> CatchUpEstimate | None:
    """The one estimate of every reported line, or None when no target has anything paid to do."""
    lines = [target.line for target in targets if target.line is not None]
    if not lines:
        return None
    failed = [(target.repository, target.free_failure) for target in targets if target.free_failure is not None]
    sign_ins: dict[str, str] = {}
    for line in lines:
        sign_ins |= line.estimate.sign_ins
    overview = None
    if sum(line.digest_possible for line in lines) >= 2:
        provider, model = model_choice(team.models.overview)
        overview = estimate_call(connection, "overview", provider, model, settings.estimates, settings.prices)
        if overview_writer is None and provider not in sign_ins:
            status = provider_status(provider, settings)
            if status.ready:
                sign_ins[provider] = status.method
    readings = plan_usage(connection) + [reading for line in lines for reading in line.estimate.plan_usage]
    return CatchUpEstimate(lines, overview, failed, team.catch_up.max_budget_usd, sign_ins, _latest_readings(readings))


def _overview(
    paths: Paths,
    name: str,
    team: TeamConfig,
    settings: GlobalConfig,
    connection: sqlite3.Connection,
    digests: list[OverviewDigest],
    budget: SharedBudget,
    stop: threading.Event,
    overview_writer: Assistant | None,
) -> OverviewOutcome:
    """Writes the overview of the catch-up's digests, under the team's budget; stopping the server cancels it."""
    if stop.is_set():
        return OverviewOutcome(None, OVERVIEW_STOPPED)
    try:
        writer: Assistant
        if overview_writer is None:
            require_provider_ready(model_choice(team.models.overview)[0], settings)
            workspace = paths.team_data(name) / "workspace"  # the call has no tools, so it reads nothing here
            workspace.mkdir(parents=True, exist_ok=True)
            writer = build_overview_assistant(workspace, settings, team.models.overview,
                                              team.catch_up.max_budget_usd_per_call)  # fmt: skip
        else:
            writer = overview_writer
        # The files are at overviews/overviews/<id>.md: an id carries its folder, as a guide page's id does.
        overviews = Overviews(paths.team_data(name) / "overviews")
        usage = UsageLog(connection, settings.prices)
        scanner = SecretScanner(settings.tools)
        return anyio.run(until_stopped, lambda: write_overview(name, digests, writer, settings, scanner, overviews,
                                                               usage, budget), stop)  # fmt: skip
    except CodetrailError as error:  # a provider that isn't ready, or the overview's commit failing
        return OverviewOutcome(None, OVERVIEW_STOPPED if stop.is_set() else str(error))
    except Exception as error:  # the catch-up is still recorded; details stay in the log
        logger.warning("Team %r's overview failed: %s", name, type(error).__name__, exc_info=True)
        return OverviewOutcome(None, OVERVIEW_FAILED.format(error=type(error).__name__))
