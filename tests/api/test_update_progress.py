"""The update's steps, for the page's update panel: numbered, bounded, and rendered inertly (design 15.4)."""

from collections.abc import Callable

from fastapi.testclient import TestClient

from codetrail.assistant.estimate import UpdateEstimate
from codetrail.config import GlobalConfig, Paths, ServerSettings
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.api.test_guide_pages import ORIGIN
from tests.api.test_guide_pages import paths as paths  # the fixture
from tests.api.test_update_estimate import wait_for

Report = Callable[[dict[str, object]], None]
HOSTILE = '<img src=x onerror="alert(1)">'
STEPS: list[dict[str, object]] = [
    {"step": "plan"},
    {"step": "page", "id": "areas/api", "title": HOSTILE, "attempt": 1},
    {"step": "page_written", "id": "areas/api", "title": HOSTILE, "provider": "claude_code",
     "model": "claude-sonnet-5-5", "tokens": 8_200, "cost_usd": 0.03},
    {"step": "page_failed", "id": "areas/web", "title": "The web", "reason": "an unverified <quote>"},
]  # fmt: skip


def reporting(steps: list[dict[str, object]]) -> Callable[[Callable[[UpdateEstimate], bool], Report], None]:
    def updater(confirm: Callable[[UpdateEstimate], bool], progress: Report) -> None:
        for step in steps:
            progress(step)

    return updater


def started(paths: Paths, steps: list[dict[str, object]], **server: int) -> TestClient:
    session = SessionState(60)
    settings = GlobalConfig(server=ServerSettings(update_cooldown_seconds=0, **server))
    app = create_app(paths, "t", session, settings, updater=reporting(steps))
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    client.headers.update({"origin": ORIGIN, TOKEN_HEADER: session.token})
    assert client.post("/update").status_code == 202
    wait_for(client, {"done"})
    return client


def test_the_status_carries_each_step_rendered_inertly(paths: Paths) -> None:
    status = started(paths, STEPS).get("/update/status").json()
    html = status["log_html"]
    assert status["next"] == 4
    assert html.count("<li") == 4
    assert "<img" not in html and "&lt;img src=x" in html
    assert "claude_code · claude-sonnet-5-5" in html and "8k tokens" in html and "$0.03" in html
    assert "an unverified &lt;quote&gt;" in html


def test_after_returns_only_the_newer_steps(paths: Paths) -> None:
    client = started(paths, STEPS)
    status = client.get("/update/status?after=3").json()
    assert status["log_html"].count("<li") == 1 and "The web" in status["log_html"]
    assert status["next"] == 4
    assert client.get("/update/status?after=4").json()["log_html"] == ""


def test_after_must_be_a_count(paths: Paths) -> None:
    client = started(paths, STEPS)
    for value in ("-1", "x", "1.5", "99999999999999999999"):
        assert client.get(f"/update/status?after={value}").status_code == 422


def test_the_log_keeps_only_its_newest_lines(paths: Paths) -> None:
    steps: list[dict[str, object]] = [{"step": "page", "id": f"p{n}", "title": f"Page {n}", "attempt": 1}
                                      for n in range(5)]  # fmt: skip
    status = started(paths, steps, update_log_lines=2).get("/update/status").json()
    assert status["next"] == 5
    assert status["log_html"].count("<li") == 2
    assert "Page 3" in status["log_html"] and "Page 4" in status["log_html"]


def test_a_new_update_starts_a_new_log(paths: Paths) -> None:
    client = started(paths, STEPS[:1])
    assert client.post("/update").status_code == 202
    wait_for(client, {"done"})
    status = client.get("/update/status").json()
    assert status["next"] == 2  # numbers keep counting, so a page reading `after` misses nothing
    assert status["log_html"].count("<li") == 1


def test_a_skipped_page_says_why_and_how_to_retry(paths: Paths) -> None:
    steps: list[dict[str, object]] = [{"step": "page_skipped", "id": "areas/root", "title": "The root"}]
    html = started(paths, steps).get("/update/status").json()["log_html"]
    assert "“The root” is skipped: it failed last time, and nothing in it changed since." in html
    assert "codetrail update t --retry-failed" in html
