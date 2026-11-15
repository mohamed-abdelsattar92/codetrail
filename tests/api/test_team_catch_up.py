"""The catch-up from the team page (design sections 24.8 and 15.4): one estimate, the ticked targets confirmed with
its single-use id, the cooldown, and the server stopping while the estimate waits.

The targets fetch from one repository served on 127.0.0.1, and their updates write with the fake assistant.
"""

import dataclasses
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from codetrail.assistant.estimate import CallEstimate, EstimateLine, UpdateEstimate
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, ServerSettings, SignalSettings, TeamSource
from codetrail.database import connect
from codetrail.guide import GuideRepository
from codetrail.hosting import HostedRepository, HostingService, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting
from codetrail.hosting.names import Service
from codetrail.lock import target_lock, team_catch_up_lock
from codetrail.team.catch_up import CatchUpEstimate, CatchUpLine
from codetrail.team.catch_up_job import CatchUpJob
from codetrail.team.server import create_team_app
from codetrail.team.settings import save_selection, write_team
from codetrail.team.state import TeamState, team_database
from codetrail.team.sync import sync_team
from codetrail.update import run_update
from codetrail.web.app import UpdateJob
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_team_add import hosted
from tests.api.test_team_server import row, section
from tests.fixtures.remote import allow_http_remotes, publish, serving_repository
from tests.fixtures.repos import add_commit, make_repository
from tests.integration.test_catch_up_across_targets import FILES, writer

ORIGIN = "http://127.0.0.1:8765"
SETTINGS = GlobalConfig(signal=SignalSettings(cache_seconds=0))
TARGETS = ["acme-payments-api", "acme-web-shop"]
HOSTILE = "<script>x</script>"
WAIT_SECONDS = 60  # the free part clones and reads each target's facts


@dataclass
class Team:
    paths: Paths
    url: str
    services: dict[Service, HostingService]
    writers: defaultdict[str, FakeAssistant]
    overview_writer: FakeAssistant

    def requests(self) -> int:
        """How many calls the assistants were asked for: none until the reader confirms."""
        return sum(len(found.requests) for found in self.writers.values()) + len(self.overview_writer.requests)

    def guide(self, target: str) -> GuideRepository:
        return GuideRepository(self.paths.target_data(target) / "guide")


def hosting(*listed: HostedRepository) -> dict[Service, HostingService]:
    status = HostingStatus("github", True, True, "reader", None)
    return {"github": FakeHosting("github", [status], [Source("github", "acme-org", None)], {"acme-org": listed}, {})}


@pytest.fixture
def team(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Team]:
    """Team acme follows acme-org, which lists web-shop and payments-api; neither has a guide yet."""
    allow_http_remotes(monkeypatch, tmp_path)
    monkeypatch.setattr("codetrail.hosting.check_clone_url", lambda service, url: url)
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "checkout", [FILES])
    with serving_repository(checkout, tmp_path / "served") as url:
        write_team(paths, "acme")
        save_selection(paths, "acme", TeamSource(service="github", source="acme-org"))
        services = hosting(hosted(url, "R_1"), hosted(url, "R_2"))
        assert sorted(sync_team(paths, "acme", services, datetime.now(UTC)).added) == TARGETS
        yield Team(paths, url, services, defaultdict(writer), FakeAssistant())


@dataclass
class Page:
    client: TestClient
    token: str

    def start(self) -> Response:
        return self.post_json("/team/catch-up", {})

    def status(self, after: int = 0) -> dict[str, object]:
        response = self.client.get(f"/team/catch-up/status?after={after}", headers={TOKEN_HEADER: self.token})
        assert response.status_code == 200, response.text
        found: dict[str, object] = response.json()
        return found

    def post_json(self, path: str, body: object) -> Response:
        response: Response = self.client.post(path, json=body, headers={"origin": ORIGIN, TOKEN_HEADER: self.token})
        return response

    def confirm(self, estimate_id: object, targets: list[str]) -> Response:
        return self.post_json("/team/catch-up/confirm", {"estimate_id": estimate_id, "targets": targets})

    def cancel(self, estimate_id: object) -> Response:
        return self.post_json("/team/catch-up/cancel", {"estimate_id": estimate_id})

    def wait_for(self, *states: str) -> dict[str, object]:
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            status = self.status()
            if status["state"] in states:
                return status
            time.sleep(0.05)
        raise AssertionError(f"the catch-up never reached {states}: {status}")

    def log(self) -> str:
        return str(self.status()["log_html"])


@contextmanager
def served(team: Team, settings: GlobalConfig = SETTINGS) -> Iterator[Page]:
    """The team's app, signed in; leaving it stops the server, as a shutdown does."""
    session = SessionState(60)
    app = create_team_app(
        team.paths, "acme", session, settings, team.services, lambda target: lambda: None,
        writer_for=team.writers.__getitem__, overview_writer=team.overview_writer,
    )  # fmt: skip
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
        yield Page(client, session.token)


def waiting(page: Page) -> dict[str, object]:
    assert page.start().status_code == 202
    return page.wait_for("waiting")


def test_catch_up_needs_token(team: Team) -> None:
    with served(team) as page:
        assert page.client.post("/team/catch-up", json={}, headers={"origin": ORIGIN}).status_code == 403
        assert page.client.get("/team/catch-up/status").status_code == 403  # it holds the estimate's id

        assert page.status()["state"] == "idle"


def test_catch_up_confirm_unknown_target_refused(team: Team) -> None:
    with served(team) as page:
        estimate_id = waiting(page)["estimate_id"]

        assert page.confirm(estimate_id, ["acme-web-shop", "acme-elsewhere"]).status_code == 400
        assert page.confirm(estimate_id, ["../web-shop"]).status_code == 422

        still = page.status()
        assert still["state"] == "waiting" and still["estimate_id"] == estimate_id  # a valid decision may still come
        assert team.requests() == 0
        assert page.cancel(estimate_id).status_code == 204
        assert page.wait_for("declined")["state"] == "declined"
    assert team.requests() == 0


def test_catch_up_confirm_duplicate_target_refused(team: Team) -> None:
    with served(team) as page:
        estimate_id = waiting(page)["estimate_id"]

        assert page.confirm(estimate_id, ["acme-web-shop", "acme-web-shop"]).status_code == 400

        assert page.status()["estimate_id"] == estimate_id
        assert page.cancel(estimate_id).status_code == 204
        page.wait_for("declined")
    assert team.requests() == 0


def test_catch_up_confirm_expired_id_refused(team: Team) -> None:
    settings = SETTINGS.model_copy(update={"server": ServerSettings(estimate_ttl_seconds=1)})
    with served(team, settings) as page:
        estimate_id = waiting(page)["estimate_id"]

        declined = page.wait_for("declined")

        assert "expired" in str(declined["message"])
        assert page.confirm(estimate_id, TARGETS).status_code == 400
        assert page.cancel(estimate_id).status_code == 400
    assert team.requests() == 0


def test_catch_up_cooldown(team: Team) -> None:
    with served(team) as page:
        estimate_id = waiting(page)["estimate_id"]
        busy = page.start()
        assert busy.status_code == 409 and "already running" in busy.json()["message"]
        assert page.confirm("not-the-id", TARGETS).status_code == 400
        assert page.cancel(estimate_id).status_code == 204
        page.wait_for("declined")

        again = page.start()

        assert again.status_code == 409 and "a moment ago" in again.json()["message"]
        assert page.status()["state"] == "declined"


def hostile_estimate(budget_usd: float = 20.0) -> CatchUpEstimate:
    def update(expected_usd: float, maximum_usd: float, digest: bool) -> UpdateEstimate:
        lines = [EstimateLine(CallEstimate("write", "claude_code", "claude-sonnet-5-5", 10_000, 1_000,
                                           expected_usd, True), 1, round(maximum_usd / expected_usd))]  # fmt: skip
        if digest:
            digest_call = CallEstimate("digest", "claude_code", "claude-sonnet-5-5", 0, 0, 0.0, True)
            lines.append(EstimateLine(digest_call, 1, 1))
        return UpdateEstimate(lines, {"claude_code": "Claude subscription (max)"}, [], 10.0, 5_000_000)

    lines = [
        CatchUpLine("acme-web-shop", HOSTILE, update(0.25, 0.75, True), False, True, True),
        CatchUpLine("acme-payments-api", "payments-api", update(0.5, 1.0, True), True, True, True),
        CatchUpLine("acme-inventory", "inventory", update(0.125, 0.5, True), False, False, True),
    ]
    overview = CallEstimate("overview", "claude_code", "claude-sonnet-5-5", 15_000, 1_500, 0.1, False)
    return CatchUpEstimate(lines, overview, [("ledger", f"git couldn't fetch {HOSTILE}")], budget_usd,
                           {"claude_code": "Claude subscription (max)"}, [])  # fmt: skip


def answering_with(estimate: CatchUpEstimate, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Replaces the coordinator: it reports one step, asks with `estimate` and records the answer."""
    answers: list[object] = []

    def run_catch_up(paths: Paths, name: str, confirm: Callable[[CatchUpEstimate], object], progress: Callable[
            [dict[str, object]], None], *_options: object) -> None:  # fmt: skip
        progress({"step": "facts", "repository": HOSTILE, "target": "acme-web-shop"})
        progress({"step": "target_failed", "repository": HOSTILE, "target": "acme-web-shop",
                  "reason": f"git couldn't fetch {HOSTILE}"})  # fmt: skip
        answers.append(confirm(estimate))

    monkeypatch.setattr("codetrail.team.server.run_catch_up", run_catch_up)
    return answers


def test_catch_up_status_renders_estimate_escaped(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = answering_with(hostile_estimate(), monkeypatch)
    with served(team) as page:
        status = waiting(page)

        rendered, log = str(status["estimate_html"]), str(status["log_html"])
        assert "<bdi>&lt;script&gt;x&lt;/script&gt;</bdi>" in rendered
        assert "<bdi>ledger</bdi>: <bdi>git couldn&#39;t fetch &lt;script&gt;x&lt;/script&gt;</bdi>" in rendered
        assert "<bdi>&lt;script&gt;x&lt;/script&gt;</bdi>: " in log
        name = "<bdi>&lt;script&gt;x&lt;/script&gt;</bdi>"
        reason = "<bdi>git couldn&#39;t fetch &lt;script&gt;x&lt;/script&gt;</bdi>"
        assert f'<li class="update-line failed">{name}: Not caught up: {reason}</li>' in log
        assert "<script>x" not in rendered + log
        assert re.search(r'<input type="checkbox" name="target" value="acme-web-shop" checked', rendered)
        assert "Before catching up" in rendered and "first guide" in rendered
        assert page.cancel(status["estimate_id"]).status_code == 204
    assert answers == [None]


def number(value: str) -> float | None:
    return None if value == "" else float(value)


def total(amounts: list[float | None]) -> float | None:
    return None if None in amounts else sum(amount for amount in amounts if amount is not None)


def page_totals(rendered: str, ticked: set[str]) -> dict[str, float | int | None]:
    """The totals the page's script shows for the ticked rows, from the attributes it reads."""
    tags = re.findall(r'(<tr data-catch-up-line[^>]*>).*?value="([^"]+)"', rendered, re.DOTALL)
    rows = [dict(re.findall(r'data-([a-z-]+)="([^"]*)"', tag)) | {"target": target} for tag, target in tags]
    chosen = [row for row in rows if row["target"] in ticked]
    overview_tag = re.search(r"<tr data-overview[^>]*>", rendered)
    assert overview_tag is not None
    overview = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', overview_tag.group(0)))
    budget = re.search(r'data-budget-usd="([^"]+)"', rendered)
    assert budget is not None
    at_most = [overview] if sum(row["digest"] == "true" for row in chosen) >= 2 else []
    expected = [overview] if sum(row["digest-expected"] == "true" for row in chosen) >= 2 else []
    maximum_tokens = sum(int(row["maximum-tokens"]) for row in chosen) + sum(int(call["tokens"]) for call in at_most)
    maximum_usd = total([number(row["maximum-usd"]) for row in chosen] + [number(call["cost-usd"]) for call in at_most])
    if maximum_usd is not None:
        maximum_usd = min(maximum_usd, float(budget.group(1)))
    expected_usd = total([number(row["expected-usd"]) for row in chosen]
                         + [number(call["cost-usd"]) for call in expected])  # fmt: skip
    if expected_usd is not None and maximum_usd is not None:
        expected_usd = min(expected_usd, maximum_usd)
    expected_tokens = sum(int(row["expected-tokens"]) for row in chosen) + sum(int(call["tokens"]) for call in expected)
    return {"expected_tokens": min(expected_tokens, maximum_tokens), "maximum_tokens": maximum_tokens,
            "expected_usd": expected_usd, "maximum_usd": maximum_usd}  # fmt: skip


@pytest.mark.parametrize("budget_usd", [20.0, 1.2])  # the second caps the maximum, and the expected with it
def test_catch_up_estimate_attributes_match_totals(
    team: Team, monkeypatch: pytest.MonkeyPatch, budget_usd: float
) -> None:
    estimate = hostile_estimate(budget_usd)
    answering_with(estimate, monkeypatch)
    with served(team) as page:
        status = waiting(page)
        rendered = str(status["estimate_html"])
        ticked = {"acme-payments-api", "acme-inventory"}  # two may write a digest, one expects to

        assert page_totals(rendered, ticked) == estimate.totals(ticked)
        assert page_totals(rendered, {line.target for line in estimate.lines}) == estimate.totals(
            [line.target for line in estimate.lines]
        )
        assert page.cancel(status["estimate_id"]).status_code == 204


def test_catch_up_unticked_target_not_updated(team: Team) -> None:
    with served(team) as page:
        status = waiting(page)
        estimate = status["estimate"]
        assert isinstance(estimate, dict)
        assert sorted(line["target"] for line in estimate["lines"]) == TARGETS

        assert page.confirm(status["estimate_id"], ["acme-web-shop"]).status_code == 204

        assert page.wait_for("done", "failed", "declined")["state"] == "done"
        assert "<bdi>web-shop</bdi>: " in page.log()
    assert team.guide("acme-web-shop").read_page("areas/api") is not None
    assert team.guide("acme-payments-api").read_page("areas/api") is None
    assert team.writers["acme-payments-api"].requests == []


def test_a_catch_up_records_the_digests_it_wrote(team: Team) -> None:
    def digest_ids(target: str) -> set[str]:
        return {page.id for page in team.guide(target).pages() if page.id.startswith("digests/")}

    for target in TARGETS:  # each has a guide already, so the next update writes a digest
        run_update(team.paths, target, claude=writer(), confirm=lambda estimate: True)
    before = {target: digest_ids(target) for target in TARGETS}
    tmp_path = team.paths.config_dir.parent
    add_commit(tmp_path / "checkout", {"README.md": "The shop.\n"}, "docs: describe the shop")
    publish(tmp_path / "checkout", tmp_path / "served")
    with served(team) as page:
        status = waiting(page)
        assert page.confirm(status["estimate_id"], TARGETS).status_code == 204
        assert page.wait_for("done", "failed", "declined")["state"] == "done"

    record = TeamState(connect(team_database(team.paths, "acme"))).last_finished()
    assert record is not None and record.digests is not None
    written = []
    for target in TARGETS:
        [digest] = digest_ids(target) - before[target]
        written.append((target, digest))
    assert record.digests == written


def test_catch_up_mounts_new_targets(team: Team) -> None:
    docs_site = dataclasses.replace(hosted(team.url, "R_1"), id="R_9", name="docs-site")
    team.services = hosting(hosted(team.url, "R_1"), hosted(team.url, "R_2"), docs_site)
    with served(team) as page:
        assert page.client.get("/repos/acme-docs-site/").status_code == 404

        status = waiting(page)

        estimate = status["estimate"]
        assert isinstance(estimate, dict)
        assert "acme-docs-site" in [line["target"] for line in estimate["lines"]]
        assert page.client.get("/repos/acme-docs-site/").status_code == 200
        assert page.cancel(status["estimate_id"]).status_code == 204
        page.wait_for("declined")
        assert 'href="/repos/acme-docs-site/"' in page.client.get("/").text


def test_server_stop_while_waiting(team: Team) -> None:
    with served(team) as page:
        waiting(page)
    # Leaving the client ran the lifespan's stop: the estimate was declined and every thread ended.
    for target in TARGETS:
        with target_lock(team.paths, target):
            pass
    with team_catch_up_lock(team.paths, "acme"):
        pass
    assert team.requests() == 0


def test_an_interrupted_catch_up_stop_still_stops_every_page(team: Team, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    request_stop, wait_for_stop = UpdateJob.request_stop, UpdateJob.wait_for_stop

    def interrupted(job: CatchUpJob) -> None:
        calls.append("catch-up waited")
        raise KeyboardInterrupt  # a signal while the catch-up's cleanup runs

    def page_asked(job: UpdateJob) -> None:
        calls.append("page asked")
        request_stop(job)

    def page_waited(job: UpdateJob) -> None:
        calls.append("page waited")
        wait_for_stop(job)

    monkeypatch.setattr(CatchUpJob, "wait_for_stop", interrupted)
    monkeypatch.setattr(UpdateJob, "request_stop", page_asked)
    monkeypatch.setattr(UpdateJob, "wait_for_stop", page_waited)
    app = create_team_app(team.paths, "acme", SessionState(60), SETTINGS, team.services, lambda target: lambda: None)
    sent: list[dict[str, object]] = []

    async def lifespan() -> None:
        messages: list[dict[str, object]] = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive() -> dict[str, object]:
            return messages.pop(0)

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt):
        anyio.run(lifespan)

    # Both pages were told to stop before any wait, and waited for after the catch-up's wait was interrupted.
    assert calls == ["page asked", "page asked", "catch-up waited", "page waited", "page waited"]


def test_catch_up_confirm_replayed_id_refused(team: Team) -> None:
    with served(team) as page:
        estimate_id = waiting(page)["estimate_id"]
        assert page.confirm(estimate_id, []).status_code == 204  # nothing ticked: nothing paid runs

        assert page.confirm(estimate_id, TARGETS).status_code == 400
        assert page.cancel(estimate_id).status_code == 400

        assert page.wait_for("done", "failed", "declined")["state"] == "declined"
    assert team.requests() == 0


def test_catch_up_confirm_while_idle_refused(team: Team) -> None:
    with served(team) as page:
        assert page.confirm("an-id", TARGETS).status_code == 400
        assert page.cancel("an-id").status_code == 400

        assert page.status()["state"] == "idle"


def test_catch_up_confirm_with_no_target_declines(team: Team) -> None:
    with served(team) as page:
        status = waiting(page)

        assert page.confirm(status["estimate_id"], []).status_code == 204

        assert page.wait_for("done", "failed", "declined")["state"] == "declined"
    assert team.requests() == 0


def test_a_finished_catch_up_shows_on_the_team_page_at_once(team: Team) -> None:
    run_update(team.paths, "acme-web-shop", facts_only=True)  # fetched once: the page reads its row, and keeps it
    with served(team, GlobalConfig(signal=SignalSettings(cache_seconds=600))) as page:
        assert "Write its guide" in row(section(page.client.get("/").text, "repositories-title"), "acme-web-shop")
        status = waiting(page)
        assert page.confirm(status["estimate_id"], ["acme-web-shop"]).status_code == 204
        assert page.wait_for("done", "failed", "declined")["state"] == "done"

        after = row(section(page.client.get("/").text, "repositories-title"), "acme-web-shop")
        assert "Write its guide" not in after  # its new guide, at once
