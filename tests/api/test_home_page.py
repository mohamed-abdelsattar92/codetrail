"""The home page: a learning column beside a rail of the repository's state (design section 16.1)."""

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
PLAN = PlanDraft(
    [
        {"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []},
        {"id": "areas/jobs", "kind": "area", "title": "The jobs", "scope_paths": ["jobs"], "facts": []},
        {"id": "areas/web", "kind": "area", "title": "The web", "scope_paths": ["app"], "facts": []},
    ],
    paths=[
        {"id": "paths/start", "title": "Start here", "goal": "Learn the app.", "steps": ["areas/app", "areas/jobs"]},
        {"id": "paths/later", "title": "Then the web", "goal": "Learn the web.", "steps": ["areas/web"]},
    ],
)  # fmt: skip
FILES: Commit = {"pyproject.toml": '[project]\nname = "x"\n', "app/main.py": "from app import db\n", "app/db.py": "",
                 "jobs/run.py": ""}  # fmt: skip


def writer(request: PageRequest) -> PageDraft:
    rubric = [{"point": "db", "grounds": ["app/main.py"]}]
    checks = [{"id": "q1", "question": "What does main import?", "rubric": rubric}]
    return PageDraft(f"About {request.title}.", checks, ["app/main.py"], brief="The key file is app/main.py.")


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    return paths


def updated(paths: Paths) -> Paths:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=writer))
    return paths


def signed_in(paths: Paths) -> tuple[TestClient, dict[str, str]]:
    session = SessionState(60)
    app = create_app(paths, "t", session, GlobalConfig(), assistant_for=lambda: FakeAssistant())
    client = TestClient(app, base_url=ORIGIN)
    client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False)
    return client, {"origin": ORIGIN, TOKEN_HEADER: session.token}


def learn(client: TestClient, headers: dict[str, str], page_id: str) -> None:
    body = {"page_id": page_id, "check_id": "q1", "answer": "db"}
    assert client.post("/learn/checks", json=body, headers=headers).status_code == 200


def split(page: str) -> tuple[str, str]:
    """The learning column and the rail."""
    main, rail = page.split('<aside class="home-rail"', 1)
    return main, rail.split("</aside>", 1)[0]


def test_every_path_shows_its_progress(paths: Paths) -> None:
    client, headers = signed_in(updated(paths))
    learn(client, headers, "areas/app")
    main, _ = split(client.get("/").text)
    listed = main.split('<section class="card home-paths"', 1)[1].split("</section>", 1)[0]
    assert 'href="/pages/paths/start"' in listed and "1/2" in listed
    assert 'href="/pages/paths/later"' in listed and "0/1" in listed
    assert "1 of 3 learned" in listed  # the guide's pages, across every path


def test_each_area_shows_its_learning_state(paths: Paths) -> None:
    client, headers = signed_in(updated(paths))
    learn(client, headers, "areas/app")
    assert client.post("/learn/read", json={"page_id": "areas/jobs"}, headers=headers).status_code == 204
    main, _ = split(client.get("/").text)
    assert '<a class="tile area-tile" data-status="learned" href="/pages/areas/app">' in main
    assert '<a class="tile area-tile" data-status="read" href="/pages/areas/jobs">' in main
    assert '<a class="tile area-tile" data-status="unread" href="/pages/areas/web">' in main


def test_the_rail_holds_the_repository_s_state(paths: Paths) -> None:
    client, _ = signed_in(updated(paths))
    main, rail = split(client.get("/").text)
    assert "Continue where you left off" in main and 'href="/pages/areas/app"' in main
    assert "Up to date" in rail and "data-update-button" in rail
    assert 'aria-labelledby="documentation-title"' in rail
    assert 'aria-labelledby="repository-title"' in rail
    assert "Your saved answers" in rail
    assert 'href="/areas/app"' in rail and 'href="/areas/jobs"' in rail  # browsing the code


def test_before_the_first_update_the_rail_stays(paths: Paths) -> None:
    run_update(paths, "t", facts_only=True)
    client, _ = signed_in(paths)
    main, rail = split(client.get("/").text)
    assert "Run your first update" in main
    assert "data-update-button" in rail
    assert 'aria-labelledby="repository-title"' in rail
    assert 'href="/areas/app"' in rail
