"""The bridge: streamed answers, limits, errors and saving to the guide (design section 7.3)."""

import json
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient

from codetrail.claude import AnswerChunk, ClaudeError, QuestionRequest
from codetrail.claude.fake import FakeClaude
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.guide import GuideRepository
from codetrail.lock import target_lock
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.repos import Commit, make_repository

ORIGIN = "http://127.0.0.1:8765"
FILES: Commit = {
    "pyproject.toml": '[project]\nname = "x"\n',
    "app/main.py": "VALUE = 42\n",
    "docs/why.md": "We chose 42.\n",
}


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    run_update(paths, "t", facts_only=True)
    return paths


def make_client(paths: Paths, claude: FakeClaude) -> tuple[TestClient, dict[str, str]]:
    session = SessionState(60)
    app = create_app(paths, "t", session, GlobalConfig(), claude_for=lambda: claude)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client, {"origin": ORIGIN, TOKEN_HEADER: session.token}


def events(response_text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in response_text.splitlines() if line.strip()]


def test_an_answer_streams_then_finishes(paths: Paths) -> None:
    claude = FakeClaude(
        answers=[[AnswerChunk("VALUE is "), AnswerChunk("**42**."), AnswerChunk(done=True, files_read=["app/main.py"])]]
    )
    client, headers = make_client(paths, claude)
    response = client.post("/bridge/questions", json={"question": "What is VALUE?"}, headers=headers)
    assert response.status_code == 200
    found = events(response.text)
    assert [event["type"] for event in found] == ["text", "text", "done"]
    assert "<strong>42</strong>" in str(found[-1]["html"])
    request = claude.requests[-1]
    assert isinstance(request, QuestionRequest) and request.language == "en"


def test_questions_need_the_token(paths: Paths) -> None:
    client, _headers = make_client(paths, FakeClaude())
    assert client.post("/bridge/questions", json={"question": "x"}, headers={"origin": ORIGIN}).status_code == 403


def test_long_questions_and_unknown_pages_are_refused(paths: Paths) -> None:
    client, headers = make_client(paths, FakeClaude())
    assert client.post("/bridge/questions", json={"question": "x" * 4001}, headers=headers).status_code == 413
    assert (
        client.post("/bridge/questions", json={"question": "x", "page_id": "areas/none"}, headers=headers).status_code
        == 404
    )
    assert client.post("/bridge/questions", json={"question": "x", "extra": 1}, headers=headers).status_code == 422


def test_a_failure_ends_the_stream_with_an_error(paths: Paths) -> None:
    client, headers = make_client(paths, FakeClaude(answers=[ClaudeError("Claude didn't finish the answer (budget).")]))
    found = events(client.post("/bridge/questions", json={"question": "x"}, headers=headers).text)
    assert found == [{"type": "error", "message": "Claude didn't finish the answer (budget)."}]


class SlowClaude(FakeClaude):
    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        await anyio.sleep(0.6)
        yield AnswerChunk("late")
        yield AnswerChunk(done=True)


def test_one_question_at_a_time(paths: Paths) -> None:
    client, headers = make_client(paths, SlowClaude())
    first: list[int] = []
    thread = threading.Thread(
        target=lambda: first.append(
            client.post("/bridge/questions", json={"question": "a"}, headers=headers).status_code
        )
    )
    thread.start()
    time.sleep(0.2)
    assert client.post("/bridge/questions", json={"question": "b"}, headers=headers).status_code == 429
    thread.join()
    assert first == [200]


def test_saving_writes_the_answer_and_demotes_unverified_quotes(paths: Paths) -> None:
    body = (
        'It is 42.\n\n> [!documented] docs/why.md#L1-L1\n> "We chose 42."\n\n'
        '> [!documented] docs/why.md#L1-L1\n> "We chose 7."\n'
    )
    claude = FakeClaude(answers=[[AnswerChunk(body), AnswerChunk(done=True, files_read=["docs/why.md"])]])
    client, headers = make_client(paths, claude)
    done = events(client.post("/bridge/questions", json={"question": "Why 42?"}, headers=headers).text)[-1]
    response = client.post(f"/bridge/answers/{done['answer_id']}/save", headers=headers)
    assert response.status_code == 200
    page_id = response.json()["page_id"]
    assert str(page_id).startswith("answers/")
    page = GuideRepository(paths.target_data("t") / "guide").read_page(str(page_id))
    assert page is not None
    assert page.meta["question"] == "Why 42?" and page.meta["language"] == "en"
    assert page.body.count("[!documented]") == 1 and page.body.count("[!inferred]") == 1
    assert client.post(f"/bridge/answers/{done['answer_id']}/save", headers=headers).status_code == 404


def test_saving_waits_for_a_running_update(paths: Paths) -> None:
    claude = FakeClaude(answers=[[AnswerChunk("x"), AnswerChunk(done=True)]])
    client, headers = make_client(paths, claude)
    done = events(client.post("/bridge/questions", json={"question": "q"}, headers=headers).text)[-1]
    with target_lock(paths, "t"):
        assert client.post(f"/bridge/answers/{done['answer_id']}/save", headers=headers).status_code == 409
