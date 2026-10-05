"""The page's update: facts first (free), then the estimate, and paid work only with its id (design 15.4)."""

import time
from collections.abc import Callable

from codetrail.assistant.estimate import CallEstimate, EstimateLine, UpdateEstimate
from codetrail.config import GlobalConfig, Paths, ServerSettings
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_guide_pages import ORIGIN, client_for
from tests.api.test_guide_pages import paths as paths  # the fixture

ESTIMATE = UpdateEstimate(
    [EstimateLine(CallEstimate("write", "claude_code", "claude-sonnet-5-5", 120_000, 6_000, 0.3, False), 2, 5)],
    {"claude_code": "Claude subscription (max)"}, [], 10.0,
)  # fmt: skip


def recording_updater(decisions: list[bool]) -> Callable[[Callable[[UpdateEstimate], bool]], None]:
    def updater(confirm: Callable[[UpdateEstimate], bool]) -> None:
        decisions.append(confirm(ESTIMATE))

    return updater


def wait_for(client: object, states: set[str]) -> dict[str, object]:
    for _ in range(100):
        status = client.get("/update/status").json()  # type: ignore[attr-defined]
        if status["state"] in states:
            return dict(status)
        time.sleep(0.05)
    raise AssertionError(f"never reached {states}: {status}")


def test_the_update_waits_with_its_estimate_and_goes_on_with_its_id(paths: Paths) -> None:
    decisions: list[bool] = []
    session = SessionState(60)
    client = client_for(paths, session, updater=recording_updater(decisions))
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    assert client.post("/update", headers=headers).status_code == 202
    status = wait_for(client, {"waiting"})
    estimate = status["estimate"]
    assert isinstance(estimate, dict) and estimate["expected_tokens"] == 2 * 126_000
    assert estimate["sign_ins"] == {"claude_code": "Claude subscription (max)"}
    estimate_id = status["estimate_id"]
    assert client.post("/update/confirm", json={"estimate_id": "wrong"}, headers=headers).status_code == 428
    assert client.post("/update/confirm", json={"estimate_id": estimate_id}).status_code == 403  # no token
    assert client.post("/update/confirm", json={"estimate_id": estimate_id}, headers=headers).status_code == 200
    assert client.post("/update/confirm", json={"estimate_id": estimate_id}, headers=headers).status_code == 428
    assert wait_for(client, {"done"}) == {"state": "done", "message": ""}
    assert decisions == [True]


def test_cancelling_spends_nothing(paths: Paths) -> None:
    decisions: list[bool] = []
    session = SessionState(60)
    client = client_for(paths, session, updater=recording_updater(decisions))
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    client.post("/update", headers=headers)
    estimate_id = wait_for(client, {"waiting"})["estimate_id"]
    assert client.post("/update/cancel", json={"estimate_id": estimate_id}, headers=headers).status_code == 200
    status = wait_for(client, {"declined"})
    assert "wasn't updated" in str(status["message"]) and "estimate" not in status
    assert decisions == [False]


def test_an_unanswered_estimate_expires(paths: Paths) -> None:
    from fastapi.testclient import TestClient

    from codetrail.web.app import create_app

    decisions: list[bool] = []
    session = SessionState(60)
    settings = GlobalConfig(server=ServerSettings(estimate_ttl_seconds=1))
    app = create_app(paths, "t", session, settings, updater=recording_updater(decisions))
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    client.post("/update", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
    assert "expired" in str(wait_for(client, {"declined"})["message"])
    assert decisions == [False]


def test_nothing_to_estimate_means_no_question(paths: Paths) -> None:
    decisions: list[bool] = []

    def updater(confirm: Callable[[UpdateEstimate], bool]) -> None:
        decisions.append(confirm(UpdateEstimate([], {}, [], 10.0)))

    session = SessionState(60)
    client = client_for(paths, session, updater=updater)
    client.post("/update", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
    assert wait_for(client, {"done"})["state"] == "done"
    assert decisions == [True]


def test_the_status_carries_the_estimate_rendered_for_the_dialog(paths: Paths) -> None:
    session = SessionState(60)
    client = client_for(paths, session, updater=recording_updater([]))
    client.post("/update", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
    html = str(wait_for(client, {"waiting"})["estimate_html"])
    assert "claude_code · claude-sonnet-5-5" in html and "2 to 5" in html
    assert "~126k" in html and "$0.30" in html and "a starting guess" in html
    assert "no charge" in html and "$10.00" in html
    page = client.get("/").text
    assert "data-estimate-dialog" in page and "Go ahead" in page
