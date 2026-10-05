"""The page's routes on an updated fixture target (design sections 7.1 and 7.2)."""

from pathlib import Path

import pytest
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from fastapi.testclient import TestClient

from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import TOKEN_HEADER, SessionState
from tests.fixtures.repos import make_repository

ORIGIN = "http://127.0.0.1:8765"
FIXTURE_LOCALES = Path(__file__).resolve().parents[1] / "fixtures" / "locales"


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(
        tmp_path / "target",
        [
            {
                "services/api/pyproject.toml": '[project]\nname = "api"\ndependencies = ["fastapi==0.1"]\n',
                "services/api/app/__init__.py": "",
                "services/api/app/main.py": "from fastapi import FastAPI\nfrom app import db\n",
                "services/api/app/db.py": "x = 1\n",
                "docs/adr/0001-use-fastapi.md": "# 0001. Use FastAPI\n\n- Status: accepted\n",
                "docs/<script>alert(1)</script>.md": "hostile name\n",
                ".env": "SECRET=1\n",
            }
        ],
    )
    write_target(paths, "t", checkout, "develop")
    run_update(paths, "t", facts_only=True)
    return paths


@pytest.fixture
def locales(tmp_path: Path) -> Path:
    folder = tmp_path / "locales" / "ar" / "LC_MESSAGES"
    folder.mkdir(parents=True)
    with (FIXTURE_LOCALES / "ar" / "LC_MESSAGES" / "codetrail.po").open("rb") as source:
        catalog = read_po(source)
    with (folder / "codetrail.mo").open("wb") as target:
        write_mo(target, catalog)
    return tmp_path / "locales"


@pytest.fixture
def session() -> SessionState:
    return SessionState(60)


@pytest.fixture
def client(paths: Paths, session: SessionState, locales: Path) -> TestClient:
    app = create_app(paths, "t", session, GlobalConfig(), locales)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def test_home_shows_areas_counts_and_the_signal(client: TestClient) -> None:
    page = client.get("/").text
    assert '<html lang="en" dir="ltr">' in page
    assert "Up to date with the branch." in page
    assert 'href="/areas/services"' in page
    assert "module</span>: 3" in page


def test_an_area_shows_its_diagrams_and_files(client: TestClient) -> None:
    page = client.get("/areas/services").text
    assert '<pre class="diagram">flowchart LR' in page
    assert "app.main" in page
    assert 'href="/source/services/api/app/main.py"' in page
    assert client.get("/areas/nowhere").status_code == 404


def test_a_fact_links_its_sources_and_relations(client: TestClient) -> None:
    page = client.get("/facts/module:services/api/app/main.py").text
    assert 'href="/source/services/api/app/main.py"' in page
    assert 'href="/facts/module:services/api/app/db.py"' in page
    assert 'href="/facts/project:services/api"' in page
    assert client.get("/facts/module:missing.py").status_code == 404


def test_source_shows_visible_files_with_line_anchors(client: TestClient) -> None:
    page = client.get("/source/services/api/app/db.py").text
    assert 'id="L1"' in page
    assert "x = 1" in page


@pytest.mark.parametrize(
    "path", [".env", "../../etc/passwd", "%2e%2e/%2e%2e/etc/passwd", "services/../.env", "nope.py"]
)
def test_source_refuses_anything_not_visible(client: TestClient, path: str) -> None:
    response = client.get(f"/source/{path}")
    assert response.status_code == 404
    assert "SECRET" not in response.text


def test_hostile_names_render_inert(client: TestClient) -> None:
    page = client.get("/areas/docs").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_decisions_are_listed(client: TestClient) -> None:
    page = client.get("/decisions").text
    assert "Use FastAPI" in page
    assert "accepted" in page


def test_the_language_switches_to_right_to_left(client: TestClient, session: SessionState) -> None:
    assert 'value="ar"' in client.get("/").text
    response = client.post(
        "/settings/language", json={"language": "ar"}, headers={"origin": ORIGIN, TOKEN_HEADER: session.token}
    )
    assert response.status_code == 204
    page = client.get("/").text
    assert '<html lang="ar" dir="rtl">' in page
    assert "الرئيسية" in page  # translated
    assert "Decisions" in page  # untranslated strings fall back to English
    assert '<h1 lang="en" dir="ltr">t</h1>' in page  # guide content stays marked as English


def test_unknown_languages_are_refused(client: TestClient, session: SessionState) -> None:
    response = client.post(
        "/settings/language", json={"language": "xx"}, headers={"origin": ORIGIN, TOKEN_HEADER: session.token}
    )
    assert response.status_code == 400


def test_the_language_needs_the_token(client: TestClient) -> None:
    assert client.post("/settings/language", json={"language": "ar"}, headers={"origin": ORIGIN}).status_code == 403


def test_static_files_are_served_with_the_policy(client: TestClient) -> None:
    response = client.get("/static/page.js")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/static/vendor/mermaid.js").status_code == 200


@pytest.mark.parametrize("path", ["/static/nope.js", "/static/../x", "/nowhere"])
def test_no_unauthenticated_response_carries_the_token(
    paths: Paths, session: SessionState, locales: Path, path: str
) -> None:
    app = create_app(paths, "t", session, GlobalConfig(), locales)
    anonymous = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    response = anonymous.get(path)
    assert session.token not in response.text
    assert "content-security-policy" in response.headers


def test_errors_carry_the_security_headers(paths: Paths, session: SessionState, locales: Path) -> None:
    (paths.target_data("t") / "source.json").write_text("{ broken")
    app = create_app(paths, "t", session, GlobalConfig(), locales)
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False, raise_server_exceptions=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    response = client.get("/areas/services")
    assert response.status_code == 500
    assert response.headers["x-frame-options"] == "DENY"
    assert "Traceback" not in response.text
