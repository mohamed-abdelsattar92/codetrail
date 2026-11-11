"""One catch-up across a team's followed repositories: one question, then the ticked updates (design section 24.8).

`run_update` is replaced by a stand-in, so these tests pin the coordinator: the threads, the question and the slots.
"""

import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from codetrail.assistant import OverviewRequest
from codetrail.assistant.estimate import CallEstimate, EstimateLine, UpdateEstimate
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths, RemoteSettings, TeamSource, write_remote_target
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.generate.run import GenerationResult
from codetrail.guide import GuideRepository, Page
from codetrail.lock import TeamBusy, team_catch_up_lock
from codetrail.team import catch_up
from codetrail.team.catch_up import CatchUpEstimate, CatchUpLine, describe_catch_up, run_catch_up
from codetrail.team.settings import save_selection, write_team
from codetrail.team.state import TeamState, team_database
from codetrail.update import UpdateResult

UNUSED: Any = None  # the manifest, snapshot, diff and extraction, which the coordinator never reads
REPOSITORIES = ("repo-a", "repo-b", "repo-c")


def call(kind: str, cost_usd: float | None = 0.5, tokens: int = 1000) -> CallEstimate:
    return CallEstimate(kind, "claude_code", "claude-sonnet-5-5", tokens, 0, cost_usd, False)


def paid(digest: bool = True, cost_usd: float | None = 0.5) -> UpdateEstimate:
    lines = [EstimateLine(call("write", cost_usd), 1, 2)]
    if digest:
        lines.append(EstimateLine(call("digest", cost_usd), 1, 1))
    return UpdateEstimate(lines, {"claude_code": "Claude subscription"}, [], 5.0, 1_000_000)


FREE = UpdateEstimate([], {}, [], 5.0, 1_000_000)


def possible() -> UpdateEstimate:
    """A page or none, and a digest only if commits came in and no page was written: 0 to 1 digests."""
    lines = [EstimateLine(call("write"), 1, 2), EstimateLine(call("digest"), 0, 1)]
    return UpdateEstimate(lines, {"claude_code": "Claude subscription"}, [], 5.0, 1_000_000)


def team_paths(tmp_path: Path, concurrency: int = 2, budget_usd: float = 20.0) -> Paths:
    """Team acme follows all of acme-org, whose repo-a, repo-b and repo-c are targets acme-repo-a and so on."""
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_team(paths, "acme")
    save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
    file = paths.team_file("acme")
    file.write_text(file.read_text() + f"\n[catch_up]\nmax_budget_usd = {budget_usd}\nconcurrency = {concurrency}\n")
    for number, repository in enumerate(REPOSITORIES, start=1):
        remote = RemoteSettings(service="github", source="acme-org", id=f"R_{number}", name=repository,
                                url=f"https://github.com/acme-org/{repository}.git")  # fmt: skip
        write_remote_target(paths, f"acme-{repository}", remote, "develop", "acme")
    return paths


@dataclass
class StandIn:
    """Stands in for `run_update`: reports its estimate to `confirm`, then "writes" when it's confirmed."""

    estimates: dict[str, UpdateEstimate] = field(default_factory=dict)  # by target; a paid estimate otherwise
    failures: dict[str, Exception] = field(default_factory=dict)  # raised before `confirm`
    in_paid_part: Callable[[str], None] = lambda target: None
    digests: bool = False  # each paid part writes a digest page
    written: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self, paths: Paths, name: str, **keywords: Any) -> UpdateResult:
        if name in self.failures:
            raise self.failures[name]
        keywords["progress"]({"step": "facts"})
        estimate = self.estimates.get(name, paid())
        if not keywords["confirm"](estimate):
            return UpdateResult(UNUSED, UNUSED, UNUSED, UNUSED, None, estimate, declined=True)
        self.in_paid_part(name)
        with self.lock:
            self.written.append(name)
        digest = None
        if self.digests:
            digest = f"digests/2026-11-11-{name}"
            GuideRepository(paths.target_data(name) / "guide").write_page(
                Page(digest, {"kind": "digest", "title": f"Changes in {name}"}, "Things changed.")
            )
        return UpdateResult(UNUSED, UNUSED, UNUSED, UNUSED, GenerationResult(digest=digest), estimate)


def install(monkeypatch: pytest.MonkeyPatch, stand_in: StandIn) -> StandIn:
    monkeypatch.setattr(catch_up, "run_update", stand_in)
    return stand_in


def asking(
    seen: list[CatchUpEstimate], answer: Callable[[CatchUpEstimate], Collection[str] | None] | None = None
) -> Callable[[CatchUpEstimate], Collection[str] | None]:
    """Records each estimate and ticks every line, or answers as `answer` does."""

    def confirm(estimate: CatchUpEstimate) -> Collection[str] | None:
        seen.append(estimate)
        return answer(estimate) if answer else [line.target for line in estimate.lines]

    return confirm


def catch_up_of(
    paths: Paths,
    confirm: Callable[[CatchUpEstimate], Collection[str] | None],
    overview_writer: FakeAssistant | None = None,
    **keywords: Any,
) -> Any:
    return run_catch_up(paths, "acme", confirm, overview_writer=overview_writer or FakeAssistant(), **keywords)


def test_one_question_for_every_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())
    seen: list[CatchUpEstimate] = []
    steps: list[dict[str, object]] = []

    result = catch_up_of(team_paths(tmp_path), asking(seen), progress=steps.append)

    assert len(seen) == 1
    assert [(line.target, line.repository) for line in seen[0].lines] == [
        ("acme-repo-a", "repo-a"), ("acme-repo-b", "repo-b"), ("acme-repo-c", "repo-c")]  # fmt: skip
    assert sorted(stand_in.written) == ["acme-repo-a", "acme-repo-b", "acme-repo-c"]
    assert result.updated == ["acme-repo-a", "acme-repo-b", "acme-repo-c"] and not result.declined
    assert steps[0] == {"step": "catch_up_started"}
    assert {"step": "facts", "repository": "repo-b", "target": "acme-repo-b"} in steps
    assert steps[-1] == {"step": "catch_up_done", "updated": 3, "failed": 0}


def test_unticked_target_keeps_free_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())
    seen: list[CatchUpEstimate] = []
    paths = team_paths(tmp_path)

    result = catch_up_of(paths, asking(seen, lambda estimate: ["acme-repo-a", "acme-repo-c"]))

    record = TeamState(connect(team_database(paths, "acme"))).last_finished()
    assert record is not None and record.unticked == ["acme-repo-b"]  # its card says it was left out

    assert stand_in.written.count("acme-repo-b") == 0
    assert "acme-repo-b" not in result.updated
    assert result.ticked == ["acme-repo-a", "acme-repo-c"]
    assert sorted(result.updated) == ["acme-repo-a", "acme-repo-c"]


def test_failed_free_part_still_asks_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn(failures={"acme-repo-c": CodetrailError("fetch refused")}))
    seen: list[CatchUpEstimate] = []

    result = catch_up_of(team_paths(tmp_path), asking(seen))

    [estimate] = seen
    assert [line.target for line in estimate.lines] == ["acme-repo-a", "acme-repo-b"]
    assert estimate.failed == [("repo-c", "fetch refused")]
    assert result.failed == [("repo-c", "fetch refused")]
    assert result.updated == ["acme-repo-a", "acme-repo-b"]


def test_the_record_says_why_each_target_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn(failures={"acme-repo-c": CodetrailError("fetch refused")}))
    paths = team_paths(tmp_path)

    catch_up_of(paths, asking([]))

    record = TeamState(connect(team_database(paths, "acme"))).last_finished()
    assert record is not None and record.failed == {"acme-repo-c": "fetch refused"}  # by target, for its card


def test_an_unexpected_error_fails_its_target_with_a_plain_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, StandIn(failures={"acme-repo-a": RuntimeError("/private/path in a traceback")}))
    seen: list[CatchUpEstimate] = []

    catch_up_of(team_paths(tmp_path), asking(seen))

    [(repository, reason)] = seen[0].failed
    assert repository == "repo-a" and "RuntimeError" in reason and "/private/path" not in reason


def test_confirm_with_unknown_target_declines_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())

    result = catch_up_of(team_paths(tmp_path), asking([], lambda estimate: {"nope"}))

    assert result.declined and result.updated == [] and result.ticked == []
    assert stand_in.written == []


def test_confirm_naming_a_target_twice_declines_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())

    result = catch_up_of(team_paths(tmp_path), asking([], lambda estimate: ["acme-repo-a", "acme-repo-a"]))

    assert result.declined and stand_in.written == []


def test_declining_runs_no_paid_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())
    paths = team_paths(tmp_path)

    result = catch_up_of(paths, asking([], lambda estimate: None))

    assert result.declined and stand_in.written == [] and result.updated == []
    record = TeamState(connect(team_database(paths, "acme"))).last_finished()
    assert record is not None and record.unticked == ["acme-repo-a", "acme-repo-b", "acme-repo-c"]


def test_no_lines_no_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn(estimates=dict.fromkeys(
        ("acme-repo-a", "acme-repo-b", "acme-repo-c"), FREE)))  # fmt: skip
    seen: list[CatchUpEstimate] = []

    result = catch_up_of(team_paths(tmp_path), asking(seen))

    assert seen == []
    assert result.estimate is None and not result.declined
    assert sorted(stand_in.written) == ["acme-repo-a", "acme-repo-b", "acme-repo-c"]  # nothing paid, so nothing asked


def test_stop_while_waiting_declines_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())
    stop = threading.Event()

    def stop_then_tick(estimate: CatchUpEstimate) -> Collection[str]:
        stop.set()
        return [line.target for line in estimate.lines]

    before = set(threading.enumerate())

    result = catch_up_of(team_paths(tmp_path), asking([], stop_then_tick), stop=stop)

    assert result.declined and result.updated == []
    assert stand_in.written == []
    assert set(threading.enumerate()) <= before  # every target's thread has ended


def test_a_confirm_that_raises_releases_every_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())

    def broken(estimate: CatchUpEstimate) -> Collection[str]:
        raise CodetrailError("the question failed")

    before = set(threading.enumerate())

    with pytest.raises(CodetrailError, match="the question failed"):
        catch_up_of(team_paths(tmp_path), broken)

    assert stand_in.written == []
    assert set(threading.enumerate()) <= before


def test_paid_concurrency_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With concurrency 1, a ticked target waits for the paid slot of the one in its paid part.

    The first target in its paid part stays there until another target has to wait for a slot, so the test proves
    the wait happens rather than hoping two threads overlap; with no bound, nobody waits and the first never
    leaves (the timeout only turns that into a failure instead of a hang)."""
    inside: list[str] = []
    most = [0]
    guard = threading.Lock()
    blocked_while_inside = threading.Event()
    real_semaphore = threading.Semaphore

    class WatchedSemaphore(real_semaphore):  # type: ignore[valid-type,misc]
        def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
            if super().acquire(blocking=False):
                return True
            with guard:
                if inside:  # free slots are all taken before anyone is ticked, so only a paid slot waits here
                    blocked_while_inside.set()
            return bool(super().acquire(blocking, timeout))

    def in_paid_part(target: str) -> None:
        with guard:
            inside.append(target)
            most[0] = max(most[0], len(inside))
        waited = blocked_while_inside.wait(timeout=30)
        with guard:
            inside.remove(target)
        assert waited, "no target waited for a paid slot"

    monkeypatch.setattr(threading, "Semaphore", WatchedSemaphore)  # only the coordinator makes semaphores here
    install(monkeypatch, StandIn(in_paid_part=in_paid_part))

    result = catch_up_of(team_paths(tmp_path, concurrency=1), asking([]))

    assert most[0] == 1
    assert result.failed == []
    assert sorted(result.updated) == ["acme-repo-a", "acme-repo-b", "acme-repo-c"]


def test_two_paid_slots_run_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With concurrency 2, two targets are in their paid parts at once: the barrier lets neither go on alone."""
    together = threading.Barrier(2, timeout=30)  # the timeout only keeps a broken coordinator from hanging the run

    def in_paid_part(target: str) -> None:
        together.wait()

    install(monkeypatch, StandIn(in_paid_part=in_paid_part))
    paths = team_paths(tmp_path, concurrency=2)
    (paths.target_file("acme-repo-c")).unlink()  # two targets, so both slots fill

    result = catch_up_of(paths, asking([]))

    assert sorted(result.updated) == ["acme-repo-a", "acme-repo-b"]


def test_stopped_targets_are_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from codetrail.team.sync import TeamSync

    stand_in = install(monkeypatch, StandIn())
    seen: list[CatchUpEstimate] = []

    catch_up_of(team_paths(tmp_path), asking(seen), sync=lambda: TeamSync(stopped=["acme-repo-b"]))

    assert [line.target for line in seen[0].lines] == ["acme-repo-a", "acme-repo-c"]
    assert "acme-repo-b" not in stand_in.written


def test_first_guide_is_marked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn())
    paths = team_paths(tmp_path)
    guide = GuideRepository(paths.target_data("acme-repo-a") / "guide")
    guide.ensure()
    guide.write_page(Page("concepts/orders", {"kind": "concept", "title": "Orders"}, "How orders flow."))
    seen: list[CatchUpEstimate] = []

    catch_up_of(paths, asking(seen))

    assert {line.target: line.first_guide for line in seen[0].lines} == {
        "acme-repo-a": False, "acme-repo-b": True, "acme-repo-c": True}  # fmt: skip


def line_of(target: str, estimate: UpdateEstimate, digest: bool, possible: bool | None = None) -> CatchUpLine:
    return CatchUpLine(target, target.removeprefix("acme-"), estimate, first_guide=False, digest_expected=digest,
                       digest_possible=digest if possible is None else possible)  # fmt: skip


def test_totals_overview_only_with_two_digests() -> None:
    overview = call("overview", cost_usd=0.25, tokens=100)
    estimate = CatchUpEstimate(
        [line_of("acme-repo-a", paid(), True), line_of("acme-repo-b", paid(), True),
         line_of("acme-repo-c", paid(digest=False), False)],
        overview, [], 100.0, {}, [],
    )  # fmt: skip

    both = estimate.totals(["acme-repo-a", "acme-repo-b"])
    one = estimate.totals(["acme-repo-a", "acme-repo-c"])

    assert both["expected_usd"] == pytest.approx(0.5 + 0.5 + 0.5 + 0.5 + 0.25)
    assert both["expected_tokens"] == 4 * 1000 + 100
    assert one["expected_usd"] == pytest.approx(0.5 + 0.5 + 0.5)  # one digest: no overview
    assert one["maximum_tokens"] == (2000 + 1000) + 2000


def test_totals_capped_by_team_budget() -> None:
    estimate = CatchUpEstimate(
        [line_of("acme-repo-a", paid(), True), line_of("acme-repo-b", paid(), True)], None, [], 2.0, {}, []
    )

    totals = estimate.totals(["acme-repo-a", "acme-repo-b"])

    assert totals["maximum_usd"] == 2.0  # 3.0 at most, but the team's budget stops it at 2.0
    assert totals["expected_usd"] == pytest.approx(2.0)


def test_totals_without_a_price_have_no_dollars() -> None:
    estimate = CatchUpEstimate([line_of("acme-repo-a", paid(cost_usd=None), True)], None, [], 2.0, {}, [])

    totals = estimate.totals(["acme-repo-a"])

    assert totals["expected_usd"] is None and totals["maximum_usd"] is None
    assert totals["expected_tokens"] == 2000


def test_describe_catch_up_names_repositories() -> None:
    estimate = CatchUpEstimate(
        [CatchUpLine("acme-repo-a", "repo-a", paid(), first_guide=True, digest_expected=True, digest_possible=True),
         line_of("acme-repo-b", paid(), True)],
        call("overview", cost_usd=0.25), [("repo-c", "fetch refused")], 20.0,
        {"claude_code": "Claude subscription"}, [],
    )  # fmt: skip

    text = "\n".join(describe_catch_up(estimate))

    assert "repo-a (first guide): write" in text and "repo-b: digest" in text
    assert "overview" in text
    assert "repo-c: fetch refused" in text
    assert "$20.00" in text
    assert "Claude subscription, so no charge" in text


def test_estimate_as_json_has_every_line() -> None:
    estimate = CatchUpEstimate(
        [line_of("acme-repo-a", paid(), True), line_of("acme-repo-b", possible(), False, possible=True)],
        call("overview", cost_usd=0.25), [("repo-c", "fetch refused")], 20.0, {}, [],
    )  # fmt: skip

    data = estimate.as_json()

    lines = data["lines"]
    assert isinstance(lines, list) and [line["target"] for line in lines] == ["acme-repo-a", "acme-repo-b"]
    assert [(line["digest_expected"], line["digest_possible"]) for line in lines] == [(True, True), (False, True)]
    assert data["failed"] == [{"repository": "repo-c", "reason": "fetch refused"}]
    assert data["budget_usd"] == 20.0
    assert isinstance(data["overview"], dict) and data["overview"]["kind"] == "overview"


def test_one_catch_up_per_team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stand_in = install(monkeypatch, StandIn())
    paths = team_paths(tmp_path)

    with team_catch_up_lock(paths, "acme"), pytest.raises(TeamBusy):
        catch_up_of(paths, asking([]))

    assert stand_in.written == []


def test_totals_count_a_possible_digest_in_the_maximum_only() -> None:
    overview = call("overview", cost_usd=0.25, tokens=100)
    estimate = CatchUpEstimate(
        [line_of("acme-repo-a", possible(), False, possible=True), line_of("acme-repo-b", possible(), False,
                                                                            possible=True)],
        overview, [], 100.0, {}, [],
    )  # fmt: skip

    totals = estimate.totals(["acme-repo-a", "acme-repo-b"])

    assert totals["expected_usd"] == pytest.approx(2 * 0.5)  # neither expects a digest, so no overview expected
    assert totals["expected_tokens"] == 2 * 1000
    assert totals["maximum_usd"] == pytest.approx(2 * (1.0 + 0.5) + 0.25)  # both may write one: the overview may run
    assert totals["maximum_tokens"] == 2 * 3000 + 100


def test_totals_cap_expected_at_the_maximum() -> None:
    lines = [EstimateLine(call("write"), 5, 5)]  # 5 calls of 1000 tokens at $0.50, but its own cap is 3000 tokens
    estimate = CatchUpEstimate(
        [line_of("acme-repo-a", UpdateEstimate(lines, {}, [], 5.0, 3000), False)], None, [], 2.0, {}, []
    )

    totals = estimate.totals(["acme-repo-a"])

    assert totals["maximum_usd"] == 2.0 and totals["expected_usd"] == 2.0  # never ~$2.50 (at most $2.00)
    assert totals["maximum_tokens"] == 3000 and totals["expected_tokens"] == 3000


def test_a_possible_digest_gets_the_overview_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn(estimates=dict.fromkeys(("acme-repo-a", "acme-repo-b", "acme-repo-c"), possible())))
    seen: list[CatchUpEstimate] = []

    catch_up_of(team_paths(tmp_path), asking(seen))

    [estimate] = seen
    assert estimate.overview is not None
    assert all(line.digest_possible and not line.digest_expected for line in estimate.lines)


def test_no_overview_in_the_estimate_means_no_overview_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn(estimates=dict.fromkeys(
        ("acme-repo-a", "acme-repo-b", "acme-repo-c"), paid(digest=False)), digests=True))  # fmt: skip
    overview_writer = FakeAssistant()
    seen: list[CatchUpEstimate] = []

    result = catch_up_of(team_paths(tmp_path), asking(seen), overview_writer=overview_writer)

    assert seen[0].overview is None
    assert overview_writer.requests == [] and result.overview is None  # three digests, but the reader saw no overview


def test_no_overview_when_fewer_than_two_ticked_lines_may_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, StandIn(digests=True))
    overview_writer = FakeAssistant()

    result = catch_up_of(team_paths(tmp_path), asking([], lambda estimate: ["acme-repo-a"]),
                         overview_writer=overview_writer)  # fmt: skip

    assert overview_writer.requests == [] and result.overview is None


def test_no_overview_when_one_ticked_line_may_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The estimate shows the overview (repo-a and repo-b may digest) and two digests are written, but of the ticked
    repo-a and repo-c only one may digest, so the reader's totals left the overview out and it doesn't run."""
    install(monkeypatch, StandIn(estimates={"acme-repo-a": paid(), "acme-repo-b": paid(),
                                            "acme-repo-c": paid(digest=False)}, digests=True))  # fmt: skip
    overview_writer = FakeAssistant()
    seen: list[CatchUpEstimate] = []

    result = catch_up_of(team_paths(tmp_path), asking(seen, lambda estimate: ["acme-repo-a", "acme-repo-c"]),
                         overview_writer=overview_writer)  # fmt: skip

    assert seen[0].overview is not None
    assert sorted(result.updated) == ["acme-repo-a", "acme-repo-c"]  # each wrote a digest
    assert not any(isinstance(request, OverviewRequest) for request in overview_writer.requests)
    assert result.overview is None


def test_the_overview_step_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn(digests=True))
    steps: list[dict[str, object]] = []

    result = catch_up_of(team_paths(tmp_path), asking([]), progress=steps.append)

    assert result.overview is not None and result.overview.overview_id is not None, result.overview
    assert steps.index({"step": "overview"}) == len(steps) - 2  # after every target's steps, before the end


def test_an_overview_that_raises_still_finishes_the_catch_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, StandIn(digests=True))
    paths = team_paths(tmp_path)

    result = catch_up_of(paths, asking([]), overview_writer=FakeAssistant(overviews=[RuntimeError("/private/path")]))

    assert result.overview is not None and result.overview.overview_id is None
    problem = result.overview.problem
    assert problem is not None and "RuntimeError" in problem and "/private/path" not in problem
    record = TeamState(connect(team_database(paths, "acme"))).last_finished()
    assert record is not None and record.overview_problem == problem
