"""Learning in the page: marks, checks graded by Claude, staleness, paths and catch-up (design section 8)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant import PageDraft, PageRequest, PlanDraft, Usage, Verdict
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.database import connect
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.repos import Commit, add_commit, fake_github_token, make_repository

ORIGIN = "http://127.0.0.1:8765"
RUBRIC_POINT = "the-rubric-point-text"
PLAN = PlanDraft(
    [{"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []}],
    paths=[{"id": "paths/start", "title": "Start", "goal": "Learn the app.", "steps": ["areas/app"]}],
)
FILES: Commit = {"pyproject.toml": '[project]\nname = "x"\n', "app/main.py": "from app import db\n", "app/db.py": ""}


def writer(question: str) -> object:
    def write(request: PageRequest) -> PageDraft:
        checks = [{"id": "q1", "question": question, "rubric": [{"point": RUBRIC_POINT, "grounds": ["app/main.py"]}]}]
        return PageDraft(f"The app imports db.\n\nAsked: {question}", checks, ["app/main.py"])

    return write


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=writer("What does main import?")))  # type: ignore[arg-type]
    return paths


def make_client(paths: Paths, claude: FakeAssistant) -> tuple[TestClient, dict[str, str]]:
    session = SessionState(60)
    client = TestClient(create_app(paths, "t", session, GlobalConfig(), assistant_for=lambda: claude), base_url=ORIGIN)
    client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False)
    return client, {"origin": ORIGIN, TOKEN_HEADER: session.token}


def test_a_page_shows_its_checks_but_never_the_rubric(paths: Paths) -> None:
    client, _ = make_client(paths, FakeAssistant())
    page = client.get("/pages/areas/app").text
    assert "What does main import?" in page
    assert RUBRIC_POINT not in page
    assert 'data-status="unread"' in page


def test_marking_read(paths: Paths) -> None:
    client, headers = make_client(paths, FakeAssistant())
    assert client.post("/learn/read", json={"page_id": "areas/app"}, headers=headers).status_code == 204
    assert 'data-status="read"' in client.get("/pages/areas/app").text


def test_answering_a_check_learns_the_page_and_never_returns_the_rubric(paths: Paths) -> None:
    claude = FakeAssistant(verdicts=[Verdict("pass", [RUBRIC_POINT], "Right: main imports db.")])
    client, headers = make_client(paths, claude)
    response = client.post(
        "/learn/checks", json={"page_id": "areas/app", "check_id": "q1", "answer": "db"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json() == {
        "verdict": "pass",
        "feedback": "Right: main imports db.",
        "state": "learned",
        "usage": {"tokens": 0, "cost_usd": 0.0},
    }
    assert RUBRIC_POINT not in response.text
    assert 'data-status="learned"' in client.get("/pages/areas/app").text


def test_a_malformed_verdict_is_an_error_not_a_pass(paths: Paths) -> None:
    client, headers = make_client(paths, FakeAssistant(verdicts=[Verdict("excellent", [], "Sure.")]))
    response = client.post(
        "/learn/checks", json={"page_id": "areas/app", "check_id": "q1", "answer": "x"}, headers=headers
    )
    assert response.status_code == 502
    assert 'data-status="unread"' in client.get("/pages/areas/app").text


@pytest.mark.parametrize(
    "body",
    [
        {"page_id": "areas/none", "check_id": "q1", "answer": "x"},
        {"page_id": "areas/app", "check_id": "nope", "answer": "x"},
        {"page_id": "../x", "check_id": "q1", "answer": "x"},
    ],
)
def test_unknown_pages_and_checks_are_not_found(paths: Paths, body: dict[str, str]) -> None:
    client, headers = make_client(paths, FakeAssistant())
    assert client.post("/learn/checks", json=body, headers=headers).status_code == 404


def test_a_rewritten_page_goes_stale_and_shows_what_changed(paths: Paths, tmp_path: Path) -> None:
    client, headers = make_client(paths, FakeAssistant())
    client.post("/learn/checks", json={"page_id": "areas/app", "check_id": "q1", "answer": "db"}, headers=headers)
    add_commit(tmp_path / "target", {"app/extra.py": "from app import db\n"})
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nrevise_max_changes = 0\n")  # a whole rewrite, not a revision
    run_update(paths, "t", claude=FakeAssistant(page_writer=writer("What does extra import?")))  # type: ignore[arg-type]
    page = client.get("/pages/areas/app").text
    assert 'data-status="stale"' in page
    assert "-Asked: What does main import?" in page
    assert "+Asked: What does extra import?" in page


def test_the_home_page_lists_paths_and_catch_up(paths: Paths) -> None:
    client, headers = make_client(paths, FakeAssistant())
    home = client.get("/").text
    assert 'href="/pages/paths/start"' in home
    assert "0 of 1 learned" in home
    assert "The guide was created" in home  # an unread digest
    digests = [line for line in home.splitlines() if "/pages/digests/" in line]
    digest_id = digests[0].split('href="/pages/')[1].split('"')[0]
    client.post("/learn/read", json={"page_id": digest_id}, headers=headers)
    assert "You're caught up." in client.get("/").text


def test_grading_waits_for_its_cooldown(paths: Paths) -> None:
    from codetrail.config import LearnSettings

    session = SessionState(60)
    settings = GlobalConfig(learn=LearnSettings(grading_cooldown_seconds=60))
    client = TestClient(
        create_app(paths, "t", session, settings, assistant_for=lambda: FakeAssistant()), base_url=ORIGIN
    )
    client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False)
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    body = {"page_id": "areas/app", "check_id": "q1", "answer": "x"}
    assert client.post("/learn/checks", json=body, headers=headers).status_code == 200
    assert client.post("/learn/checks", json=body, headers=headers).status_code == 429


def test_feedback_holding_a_secret_is_withheld_and_usage_recorded(paths: Paths) -> None:
    token = fake_github_token(31)
    usage = Usage("claude_code", "claude-sonnet-5-5", 300, 0, 40, 0.01)
    claude = FakeAssistant(verdicts=[Verdict("pass", [], f"Right; the key {token} works.", 0.01, usage)])
    client, headers = make_client(paths, claude)
    response = client.post(
        "/learn/checks", json={"page_id": "areas/app", "check_id": "q1", "answer": "db"}, headers=headers
    )
    assert response.status_code == 200
    assert token not in response.text and "looks like a secret" in response.json()["feedback"]
    connection = connect(paths.target_data("t") / "codetrail.db")
    stored = [row["feedback"] for row in connection.execute("SELECT feedback FROM check_attempts")]
    assert stored and token not in stored[0]
    rows = connection.execute("SELECT kind, cost_usd FROM assistant_calls").fetchall()
    assert [tuple(row) for row in rows] == [("grade", 0.01)]


def test_the_check_button_shows_its_estimate_and_the_grade_its_usage(paths: Paths) -> None:
    usage = Usage("claude_code", "claude-sonnet-5-5", 3_000, 0, 300, 0.01)
    claude = FakeAssistant(verdicts=[Verdict("pass", [], "Right.", 0.01, usage)])
    client, headers = make_client(paths, claude)
    assert "~4k tokens · ~$0.01" in client.get("/pages/areas/app").text
    response = client.post(
        "/learn/checks", json={"page_id": "areas/app", "check_id": "q1", "answer": "db"}, headers=headers
    )
    assert response.json()["usage"] == {"tokens": 3_300, "cost_usd": 0.01}


def test_marking_unread(paths: Paths) -> None:
    client, headers = make_client(paths, FakeAssistant())
    assert client.post("/learn/unread", json={"page_id": "areas/app"}, headers={"origin": ORIGIN}).status_code == 403
    client.post("/learn/read", json={"page_id": "areas/app"}, headers=headers)
    assert client.post("/learn/unread", json={"page_id": "areas/app"}, headers=headers).status_code == 204
    assert 'data-status="unread"' in client.get("/pages/areas/app").text
    assert client.post("/learn/unread", json={"page_id": "areas/missing"}, headers=headers).status_code == 404
