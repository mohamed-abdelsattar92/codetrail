"""Search from the page: the routes, what they find and never find, and their protections (design section 16.3)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant import AnswerChunk, PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.update import run_update
from codetrail.web import app as web_app
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.repos import Commit, make_repository

ORIGIN = "http://127.0.0.1:8765"
PLAN = PlanDraft(
    [{"id": "concepts/retries", "kind": "concept", "title": "Payment retries", "scope_paths": ["app"], "facts": []}]
)


def page_writer(request: PageRequest) -> PageDraft:
    checks = [{"id": "q", "question": "Why?", "rubric": [{"point": "zanzibar", "grounds": ["app/main.py"]}]}]
    return PageDraft("## Backoff\n\nA failed charge is retried with a delay.\n", checks, ["app/main.py"])


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    files: Commit = {
        "pyproject.toml": '[project]\nname = "x"\n',
        "app/main.py": "from app import charges\n",
        "app/charges.py": "",
        "private/hidden_module.py": "",
        ".codetrailignore": "private/\n",
        ".env": "SEARCHSECRETWORD=1\n",
    }
    write_target(paths, "t", make_repository(tmp_path / "target", [files]), "develop")
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=page_writer))
    return paths


def signed_in(paths: Paths, claude: FakeAssistant | None = None) -> tuple[TestClient, dict[str, str]]:
    session = SessionState(60)
    options = {"assistant_for": lambda: claude} if claude else {}
    client = TestClient(create_app(paths, "t", session, GlobalConfig(), **options), base_url=ORIGIN)  # type: ignore[arg-type]
    assert client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False).status_code == 303
    return client, {TOKEN_HEADER: session.token}


def found(client: TestClient, headers: dict[str, str], query: str) -> list[dict[str, object]]:
    response = client.get("/search/results", params={"q": query}, headers=headers)
    assert response.status_code == 200
    results: list[dict[str, object]] = response.json()["results"]
    return results


def test_both_routes_need_the_session(paths: Paths) -> None:
    client = TestClient(create_app(paths, "t", SessionState(60), GlobalConfig()), base_url=ORIGIN)
    assert client.get("/search", params={"q": "retry"}).status_code == 403
    assert client.get("/search/results", params={"q": "retry"}).status_code == 403


def test_the_json_route_needs_the_token(paths: Paths) -> None:
    client, _ = signed_in(paths)
    assert client.get("/search/results", params={"q": "retry"}).status_code == 403
    assert client.get("/search/results", params={"q": "retry"}, headers={TOKEN_HEADER: "wrong"}).status_code == 403


def test_results_find_titles_headings_and_facts(paths: Paths) -> None:
    client, headers = signed_in(paths)
    assert found(client, headers, "payment")[0]["link"] == "/pages/concepts/retries"
    assert found(client, headers, "backoff")[0]["title"] == "Payment retries"
    assert any(result["link"] == "/facts/module:app/charges.py" for result in found(client, headers, "charges"))


def test_results_carry_plain_segments(paths: Paths) -> None:
    client, headers = signed_in(paths)
    [result] = found(client, headers, "delay")
    assert ["delay", True] in result["snippet"]  # type: ignore[operator]


def test_hidden_and_excluded_files_are_never_found(paths: Paths) -> None:
    client, headers = signed_in(paths)
    for query in ("hidden_module", "private", "SEARCHSECRETWORD", "env"):
        assert all("private" not in json.dumps(result) for result in found(client, headers, query))
        assert all("SEARCHSECRETWORD" not in json.dumps(result) for result in found(client, headers, query))


def test_a_rubric_is_never_found(paths: Paths) -> None:
    client, headers = signed_in(paths)
    assert found(client, headers, "zanzibar") == []


def test_the_json_route_is_not_cached_and_takes_any_query(paths: Paths) -> None:
    client, headers = signed_in(paths)
    for query in ("it's", "'; DROP TABLE documents; --", 'title:x "', "x" * 5000, "🔍"):
        response = client.get("/search/results", params={"q": query}, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"


def test_the_results_page_renders_matches_as_escaped_text(paths: Paths) -> None:
    client, _ = signed_in(paths)
    assert "<mark>delay</mark>" in client.get("/search", params={"q": "delay"}).text
    page = client.get("/search", params={"q": "<script>alert(1)</script>"}).text
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)" in page


def test_a_saved_answer_is_found_at_once(paths: Paths) -> None:
    claude = FakeAssistant(answers=[[AnswerChunk("Charges retry with quokka backoff."), AnswerChunk(done=True)]])
    client, headers = signed_in(paths, claude)
    write_headers = {**headers, "origin": ORIGIN}
    stream = client.post("/bridge/questions", json={"question": "How do charges retry?"}, headers=write_headers)
    answer_id = [json.loads(line) for line in stream.text.splitlines() if line.strip()][-1]["answer_id"]
    assert client.post(f"/bridge/answers/{answer_id}/save", json={}, headers=write_headers).status_code == 200
    [result] = found(client, headers, "quokka")
    assert result["kind"] == "answer"


def test_a_failing_index_leaves_the_page_working(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*arguments: object) -> list[object]:
        raise OSError("disk on fire")

    monkeypatch.setattr(web_app, "guide_documents", broken)
    client, headers = signed_in(paths)
    response = client.get("/search/results", params={"q": "payment"}, headers=headers)
    assert response.json() == {"results": [], "available": False}
    assert client.get("/").status_code == 200
    assert client.get("/search", params={"q": "payment"}).status_code == 200


def test_pages_a_failed_update_discards_leave_the_index(paths: Paths) -> None:
    from codetrail.guide import GuideRepository, Page

    client, headers = signed_in(paths)
    guide = GuideRepository(paths.target_data("t") / "guide")
    guide.write_page(Page("concepts/draft", {"kind": "concept", "title": "Wombat draft"}, "Never committed."))
    assert len(found(client, headers, "wombat")) == 1  # written, not yet committed: what the page could show
    guide.discard()
    assert found(client, headers, "wombat") == []
