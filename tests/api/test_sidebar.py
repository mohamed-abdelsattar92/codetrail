"""The sidebar's pins, folding sections and toggle (design section 22)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant import PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.guide import GuideRepository, Page
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.repos import Commit, make_repository

ORIGIN = "http://127.0.0.1:8765"
PLAN = PlanDraft(
    [
        {"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []},
        {"id": "concepts/db", "kind": "concept", "title": "The database", "scope_paths": ["app"], "facts": []},
        {"id": "areas/lonely", "kind": "area", "title": "Lonely area", "scope_paths": ["app"], "facts": []},
    ],
    paths=[{"id": "paths/start", "title": "Start", "goal": "Learn the app.", "steps": ["areas/app", "concepts/db"]}],
)
FILES: Commit = {"pyproject.toml": '[project]\nname = "x"\n', "app/main.py": "from app import db\n", "app/db.py": ""}


def writer(request: PageRequest) -> PageDraft:
    checks = [{"id": "q1", "question": "Why?", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}]
    return PageDraft("## First part\n\nText.", checks, ["app/main.py"])


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "t", make_repository(tmp_path / "target", [FILES]), "develop")
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=writer))
    return paths


def signed_in(paths: Paths, settings: GlobalConfig | None = None) -> tuple[TestClient, dict[str, str]]:
    session = SessionState(60)
    client = TestClient(create_app(paths, "t", session, settings or GlobalConfig()), base_url=ORIGIN)
    assert client.get(f"/login?code={session.issue_login_code()}", follow_redirects=False).status_code == 303
    return client, {"origin": ORIGIN, TOKEN_HEADER: session.token}


def sidebar_of(page: str) -> str:
    return page.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]


def tiles_of(page: str) -> str:
    return sidebar_of(page).split("data-pins", 1)[1].split("</ul>", 1)[0]


def test_pins_are_stored_and_drawn_in_order(paths: Paths) -> None:
    client, headers = signed_in(paths)
    pages = ["concepts/db", "areas/app"]
    assert client.post("/settings/pins", json={"pages": pages}, headers=headers).status_code == 204
    tiles = tiles_of(client.get("/").text)
    assert tiles.index('href="/pages/concepts/db"') < tiles.index('href="/pages/areas/app"')
    assert ">TD</a>" in tiles and 'aria-label="The database"' in tiles


def test_pins_need_the_session_token_and_origin(paths: Paths) -> None:
    client, headers = signed_in(paths)
    body = {"pages": ["areas/app"]}
    assert client.post("/settings/pins", json=body, headers={"origin": ORIGIN}).status_code == 403
    other_origin = {**headers, "origin": "http://evil.test"}
    assert client.post("/settings/pins", json=body, headers=other_origin).status_code == 403


@pytest.mark.parametrize(
    ("pages", "status"),
    [
        (["areas/missing"], 400),
        (["module:app/db.py"], 400),
        (["areas/app", "concepts/db", "areas/lonely", "paths/start"] + [f"areas/x{n}" for n in range(6)], 400),
        (["a" * 201], 422),
    ],
)
def test_pins_refuse_what_the_guide_doesnt_hold(paths: Paths, pages: list[str], status: int) -> None:
    client, headers = signed_in(paths)
    assert client.post("/settings/pins", json={"pages": pages}, headers=headers).status_code == status


def test_pins_refuse_another_field(paths: Paths) -> None:
    client, headers = signed_in(paths)
    body = {"pages": [], "extra": 1}
    assert client.post("/settings/pins", json=body, headers=headers).status_code == 422


def test_more_pins_than_the_limit_are_refused(paths: Paths) -> None:
    client, headers = signed_in(paths, GlobalConfig.model_validate({"ui": {"max_pins": 1}}))
    body = {"pages": ["areas/app", "concepts/db"]}
    assert client.post("/settings/pins", json=body, headers=headers).status_code == 400
    twice = {"pages": ["areas/app", "areas/app"]}  # duplicates are dropped before the limit applies
    assert client.post("/settings/pins", json=twice, headers=headers).status_code == 204


def test_a_removed_page_drops_out_of_the_pins(paths: Paths) -> None:
    client, headers = signed_in(paths)
    client.post("/settings/pins", json={"pages": ["areas/lonely", "areas/app"]}, headers=headers)
    guide = GuideRepository(paths.target_data("t") / "guide")
    guide.path_of("areas/lonely").unlink()
    guide.commit("Drop an area")
    tiles = tiles_of(client.get("/").text)
    assert "areas/lonely" not in tiles and 'href="/pages/areas/app"' in tiles


def test_a_title_holding_markup_stays_text_in_its_tile(paths: Paths) -> None:
    guide = GuideRepository(paths.target_data("t") / "guide")
    page = guide.read_page("areas/app")
    assert page is not None
    guide.write_page(Page("areas/app", {**page.meta, "title": "<b>App</b>"}, page.body))
    guide.commit("Markup title")
    client, headers = signed_in(paths)
    client.post("/settings/pins", json={"pages": ["areas/app"]}, headers=headers)
    sidebar = sidebar_of(client.get("/").text)
    assert "<b>App</b>" not in sidebar and 'aria-label="&lt;b&gt;App&lt;/b&gt;"' in sidebar


def test_the_sidebar_carries_its_folds_pins_and_toggle(paths: Paths) -> None:
    client, _ = signed_in(paths)
    page = client.get("/pages/areas/app").text
    sidebar = sidebar_of(page)
    for name in ("paths", "areas", "concepts", "answers", "more"):
        assert f'data-fold="{name}" aria-expanded="true" aria-controls="nav-{name}"' in sidebar
    assert 'data-pin="areas/app" data-kind="area" data-initials="TA" aria-pressed="false"' in sidebar
    assert "data-pin-template" in sidebar and 'data-max-pins="9"' in sidebar
    assert "data-sidebar-toggle" in page and "data-sidebar-edge" in page
    assert 'href="/pages/areas/app" aria-current="page"' in sidebar


def test_a_lowered_limit_draws_only_that_many_pins(paths: Paths) -> None:
    client, headers = signed_in(paths)
    client.post("/settings/pins", json={"pages": ["concepts/db", "areas/app", "paths/start"]}, headers=headers)
    lowered, _ = signed_in(paths, GlobalConfig.model_validate({"ui": {"max_pins": 1}}))
    tiles = tiles_of(lowered.get("/").text)
    assert tiles.count('class="pin"') == 1 and 'href="/pages/concepts/db"' in tiles  # unpinning it stays possible


def test_an_answer_s_tile_keeps_the_reader_s_language(paths: Paths) -> None:
    guide = GuideRepository(paths.target_data("t") / "guide")
    meta = {"kind": "answer", "title": "لماذا تُعاد المحاولة؟", "asked_at": "2026-10-05T10:00:00+00:00"}
    guide.write_page(Page("answers/2026-10-05-retry-111111", meta, "Because charges fail."))
    guide.commit("An answer")
    client, headers = signed_in(paths)
    pages = ["answers/2026-10-05-retry-111111", "areas/app"]
    assert client.post("/settings/pins", json={"pages": pages}, headers=headers).status_code == 204
    sidebar = sidebar_of(client.get("/").text)
    answer = sidebar.split('href="/pages/answers/2026-10-05-retry-111111"', 1)[1].split(">", 1)[0]
    area = sidebar.split('<a class="pin" href="/pages/areas/app"', 1)[1].split(">", 1)[0]
    assert 'dir="auto"' in answer and 'lang="en"' not in answer
    assert 'lang="en" dir="ltr"' in area
    assert 'data-pin="answers/2026-10-05-retry-111111" data-kind="answer"' in sidebar
