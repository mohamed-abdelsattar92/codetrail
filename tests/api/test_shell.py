"""The page's shell and its new pages: landmarks, the sidebar, progress, saved answers, digests and paths (16.1)."""

import json
import re
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant import AnswerChunk, PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.guide import GuideRepository, Page
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.catalogs import compiled_locales
from tests.fixtures.repos import Commit, make_repository

ORIGIN = "http://127.0.0.1:8765"
STATIC = Path(str(files("codetrail.web").joinpath("static")))
PLAN = PlanDraft(
    [
        {"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []},
        {"id": "concepts/db", "kind": "concept", "title": "The database", "scope_paths": ["app"], "facts": []},
        {"id": "areas/lonely", "kind": "area", "title": "Lonely area", "scope_paths": ["app"], "facts": []},
    ],
    paths=[
        {"id": "paths/start", "title": "Start", "goal": "Learn the app.", "steps": ["areas/app", "concepts/db"]},
        {"id": "paths/again", "title": "Again", "goal": "Once more.", "steps": ["concepts/db", "areas/app"]},
    ],
)
FILES: Commit = {"pyproject.toml": '[project]\nname = "x"\n', "app/main.py": "from app import db\n", "app/db.py": ""}


def writer(request: PageRequest) -> PageDraft:
    checks = [{"id": "q1", "question": "Why?", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}]
    return PageDraft("## First part\n\nText.\n\n## Second part\n\nMore.", checks, ["app/main.py"])


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=writer))
    return paths


def signed_in(paths: Paths, claude: FakeAssistant | None = None) -> tuple[TestClient, dict[str, str]]:
    session = SessionState(60)
    options: dict[str, object] = {"locales": compiled_locales(paths.state_dir / "locales")}
    if claude is not None:
        options["assistant_for"] = lambda: claude
    client = TestClient(create_app(paths, "t", session, GlobalConfig(), **options), base_url=ORIGIN)  # type: ignore[arg-type]
    assert client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False).status_code == 303
    return client, {"origin": ORIGIN, TOKEN_HEADER: session.token}


class Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.handlers: list[str] = []
        self.inline: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handlers += [name for name, _ in attrs if name.startswith("on") or name == "style"]
        if tag == "script":
            self.scripts.append(dict(attrs))
            self._in_script = True

    def handle_endtag(self, tag: str) -> None:
        self._in_script = self._in_script and tag != "script"

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.inline.append(data)


PAGES = [
    "/", "/progress", "/answers", "/digests", "/decisions", "/documentation", "/search?q=app", "/pages/areas/app",
    "/pages/paths/start",
    "/facts/module:app/main.py", "/source/app/main.py", "/areas/app", "/nowhere",
]  # fmt: skip


@pytest.mark.parametrize("url", PAGES)
def test_every_page_has_the_shell(paths: Paths, url: str) -> None:
    client, _ = signed_in(paths)
    page = client.get(url).text
    for marker in ('<header class="topbar"', '<nav class="sidebar"', '<main id="content"', 'href="#content"',
                   'action="/search"', "data-ask ", "data-palette", "data-shortcuts", "data-estimate-dialog",
                   'rel="icon" href="/static/brand/favicon.svg"'):  # fmt: skip
        assert marker in page, marker


@pytest.mark.parametrize("url", PAGES)
def test_pages_run_no_inline_script_or_handler(paths: Paths, url: str) -> None:
    client, _ = signed_in(paths)
    parser = Scripts()
    parser.feed(client.get(url).text)
    assert parser.inline == [] and parser.handlers == []
    assert [script.get("src") for script in parser.scripts] == [
        "/static/js/theme-init.js", "/static/vendor/mermaid.js", "/static/js/main.js",
    ]  # fmt: skip
    assert parser.scripts[2].get("type") == "module"


def test_the_sidebar_lists_paths_areas_concepts_and_answers(paths: Paths) -> None:
    client, _ = signed_in(paths)
    sidebar = client.get("/pages/areas/app").text.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/pages/paths/start"' in sidebar and "0/2" in sidebar
    assert 'href="/pages/areas/app" aria-current="page"' in sidebar
    assert 'href="/pages/concepts/db"' in sidebar
    assert "Answers you save appear here." in sidebar
    assert 'href="/answers"' not in sidebar  # no saved answers yet


def test_a_saved_answer_reaches_the_sidebar_the_home_page_and_the_answers_page(paths: Paths) -> None:
    claude = FakeAssistant(answers=[[AnswerChunk("It reads the db."), AnswerChunk(done=True)]])
    client, headers = signed_in(paths, claude)
    stream = client.post("/bridge/questions", json={"question": "What does main read?"}, headers=headers)
    answer_id = json.loads(stream.text.strip().splitlines()[-1])["answer_id"]
    page_id = client.post(f"/bridge/answers/{answer_id}/save", json={}, headers=headers).json()["page_id"]
    for url in ("/", "/answers", "/progress"):
        page = client.get(url).text
        assert f'href="/pages/{page_id}"' in page, url
        assert "What does main read?" in page
    assert 'href="/answers"' in client.get("/").text


def test_the_answers_page_survives_malformed_front_matter_and_marks_changed_sources(paths: Paths) -> None:
    guide = GuideRepository(paths.target_data("t") / "guide")
    guide.write_page(Page("answers/2026-10-01-odd-111111", {"kind": "answer", "title": ["a", "list"],
                                                            "asked_at": 5}, "Odd."))  # fmt: skip
    stale = {"kind": "answer", "title": "Stale?", "asked_at": "2026-10-02",
             "files": [{"path": "app/main.py", "blob": "0" * 40}]}  # fmt: skip
    guide.write_page(Page("answers/2026-10-02-stale-222222", stale, "Old."))
    guide.commit("Answers")
    client, _ = signed_in(paths)
    page = client.get("/answers").text
    assert "Stale?" in page and "sources changed" in page
    assert "answers/2026-10-01-odd-111111" in page


def test_an_empty_answers_page_invites(paths: Paths) -> None:
    client, _ = signed_in(paths)
    assert "Keep the answers worth keeping" in client.get("/answers").text


def test_progress_lists_each_path_and_the_pages_outside_them(paths: Paths) -> None:
    client, _ = signed_in(paths)
    page = client.get("/progress").text
    assert 'href="/pages/areas/app?path=paths/start"' in page
    assert 'href="/pages/concepts/db?path=paths/again"' in page
    assert "Outside the paths" in page and "Lonely area" in page


def test_the_digests_page_lists_digests_with_unread_marks(paths: Paths) -> None:
    client, _ = signed_in(paths)
    page = client.get("/digests").text
    assert "The guide was created" in page and "unread" in page


def test_previous_and_next_follow_the_path_the_reader_came_from(paths: Paths) -> None:
    client, _ = signed_in(paths)
    again = client.get("/pages/concepts/db?path=paths/again").text
    assert 'href="/pages/areas/app?path=paths/again" data-pager-next' in again
    assert "data-pager-previous" not in again
    start = client.get("/pages/concepts/db?path=paths/start").text
    assert 'href="/pages/areas/app?path=paths/start" data-pager-previous' in start


@pytest.mark.parametrize("hint", ["paths/missing", "../outline", "areas/app", "%3Cscript%3E"])
def test_an_unknown_path_falls_back_to_the_first_path_holding_the_page(paths: Paths, hint: str) -> None:
    client, _ = signed_in(paths)
    response = client.get(f"/pages/concepts/db?path={hint}")
    assert response.status_code == 200
    assert 'href="/pages/areas/app?path=paths/again" data-pager-next' in response.text  # "Again" sorts first
    assert "<script>" not in response.text.split("<main", 1)[1]


def test_a_page_in_no_path_has_no_pager(paths: Paths) -> None:
    client, _ = signed_in(paths)
    page = client.get("/pages/areas/lonely").text
    assert "data-pager-next" not in page and "data-pager-previous" not in page


def test_the_home_page_continues_where_the_reader_left_off(paths: Paths) -> None:
    client, _ = signed_in(paths)
    page = client.get("/").text
    assert "Continue where you left off" in page
    assert 'href="/pages/concepts/db?path=paths/again"' in page


def test_the_shell_turns_right_to_left(paths: Paths) -> None:
    client, headers = signed_in(paths)
    client.post("/settings/language", json={"language": "ar"}, headers=headers)
    page = client.get("/pages/areas/app").text
    assert '<html lang="ar" dir="rtl">' in page
    assert '<article class="guide-page" lang="en" dir="ltr">' in page


def test_search_results_name_their_kind_in_the_reader_s_language(paths: Paths) -> None:
    client, headers = signed_in(paths)
    client.post("/settings/language", json={"language": "ar"}, headers=headers)
    page = client.get("/search?q=app").text
    assert '<span class="kind" lang="ar" dir="rtl">منطقة</span>' in page  # "Area", inside the English results


def test_an_estimate_reads_left_to_right_in_any_language(paths: Paths) -> None:
    client, headers = signed_in(paths)
    client.post("/settings/language", json={"language": "ar"}, headers=headers)
    assert '<span class="cost" dir="ltr"' in client.get("/pages/areas/app").text  # "~4k tokens · ~$0.01"


PHYSICAL = re.compile(
    r"(?<![-\w])(left|right|margin-left|margin-right|padding-left|padding-right|border-left|border-right"
    r"|border-top-left-radius|border-top-right-radius|border-bottom-left-radius|border-bottom-right-radius)\s*:"
    r"|text-align:\s*(left|right)|float:\s*(left|right)"
    r"|(?<![-\w])(padding|margin):[ \t]*[^;\s]+([ \t]+[^;\s]+){3}[ \t]*;"
)


@pytest.mark.parametrize("sheet", sorted(path.name for path in (STATIC / "css").glob("*.css")))
def test_stylesheets_use_logical_properties_only(sheet: str) -> None:
    text = re.sub(r"/\*.*?\*/", "", (STATIC / "css" / sheet).read_text(), flags=re.S)
    assert PHYSICAL.findall(text) == []


def test_a_path_links_only_steps_that_exist(paths: Paths) -> None:
    guide = GuideRepository(paths.target_data("t") / "guide")
    path = guide.read_page("paths/start")
    assert path is not None
    meta = {**path.meta, "steps": ["areas/app", "../source/app/main.py", "concepts/missing"]}
    guide.write_page(Page("paths/start", meta, path.body))
    guide.commit("Odd steps")
    client, _ = signed_in(paths)
    page = client.get("/pages/paths/start").text.split("<main", 1)[1]
    assert 'href="/pages/areas/app?path=paths/start"' in page
    assert "../source" not in page and "concepts/missing" not in page
