"""Guide pages in the browser, and the Update button (design sections 6, 7.1)."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant import PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.repos import Commit, make_repository

ORIGIN = "http://127.0.0.1:8765"
PLAN = PlanDraft([{"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []}])


def page_writer(request: PageRequest) -> PageDraft:
    body = (
        "Overview with <script>alert(1)</script> and [x](javascript:alert(1)).\n\n"
        '> [!documented] docs/adr/0001-x.md#L3-L3\n> "We chose Python."\n\n> [!inferred]\n> It reads well.\n\n'
        "{{diagram imports scope=app}}\n"
    )
    checks = [{"id": "q", "question": "Why?", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}]
    return PageDraft(body, checks, ["app/main.py"])


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    files: Commit = {
        "pyproject.toml": '[project]\nname = "x"\n',
        "app/main.py": "from app import db\n",
        "app/db.py": "",
        "docs/adr/0001-x.md": "# 0001. X\n\nWe chose Python.\n",
    }
    write_target(paths, "t", make_repository(tmp_path / "target", [files]), "develop")
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=page_writer))
    return paths


def client_for(paths: Paths, session: SessionState, **options: object) -> TestClient:
    app = create_app(paths, "t", session, GlobalConfig(), **options)  # type: ignore[arg-type]
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def test_the_home_page_lists_the_guide(paths: Paths) -> None:
    page = client_for(paths, SessionState(60)).get("/").text
    assert 'href="/pages/areas/app"' in page
    assert "The guide was created" in page


def test_a_guide_page_renders_rationale_and_diagrams_inertly(paths: Paths) -> None:
    page = client_for(paths, SessionState(60)).get("/pages/areas/app").text
    assert "<script>alert(1)</script>" not in page
    assert 'href="javascript:' not in page
    assert 'class="rationale documented"' in page
    assert 'href="/source/docs/adr/0001-x.md#L3"' in page
    assert 'class="rationale inferred"' in page
    assert '<pre class="diagram">flowchart LR' in page


@pytest.mark.parametrize("page_id", ["areas/missing", "../outline", "areas/../../x", "outline"])
def test_unknown_pages_are_not_found(paths: Paths, page_id: str) -> None:
    assert client_for(paths, SessionState(60)).get(f"/pages/{page_id}").status_code == 404


def test_the_update_button_runs_one_update_at_a_time(paths: Paths) -> None:
    calls: list[str] = []

    def updater() -> None:
        calls.append("run")
        time.sleep(0.3)

    session = SessionState(60)
    client = client_for(paths, session, updater=updater)
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    assert client.post("/update").status_code == 403  # no token
    assert client.post("/update", headers=headers).status_code == 202
    assert client.post("/update", headers=headers).status_code == 409
    for _ in range(50):
        if client.get("/update/status").json()["state"] == "done":
            break
        time.sleep(0.05)
    assert client.get("/update/status").json() == {"state": "done", "message": ""}
    assert calls == ["run"]


def test_a_failed_update_reports_without_details(paths: Paths) -> None:
    def updater() -> None:
        raise RuntimeError("secret detail")

    session = SessionState(60)
    client = client_for(paths, session, updater=updater)
    client.post("/update", headers={"origin": ORIGIN, TOKEN_HEADER: session.token})
    for _ in range(50):
        status = client.get("/update/status").json()
        if status["state"] != "running":
            break
        time.sleep(0.05)
    assert status["state"] == "failed"
    assert "secret detail" not in status["message"]


def test_updates_from_the_page_wait_for_the_cooldown(paths: Paths) -> None:
    from codetrail.config import ServerSettings

    session = SessionState(60)
    settings = GlobalConfig(server=ServerSettings(update_cooldown_seconds=300))
    app = create_app(paths, "t", session, settings, updater=lambda: None)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    assert client.post("/update", headers=headers).status_code == 202
    for _ in range(50):
        if client.get("/update/status").json()["state"] == "done":
            break
        time.sleep(0.05)
    assert client.post("/update", headers=headers).status_code == 429
