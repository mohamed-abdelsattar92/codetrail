"""The system page, the home card, the sidebar link and the focused diagram on area pages (design 17.4)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.fixtures.repos import MIXED_REPOSITORY, Commit, make_repository

ORIGIN = "http://127.0.0.1:8765"


def client_for(tmp_path: Path, files: Commit) -> TestClient:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "shop", make_repository(tmp_path / "target", [files]), "develop")
    run_update(paths, "shop", facts_only=True)
    session = SessionState(60)
    client = TestClient(create_app(paths, "shop", session, GlobalConfig()), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return client_for(tmp_path, MIXED_REPOSITORY)


def test_the_system_page_draws_and_explains_the_system(client: TestClient) -> None:
    page = client.get("/system").text
    assert '<pre class="diagram">flowchart LR' in page
    assert "shape: doc" in page  # the contract
    assert "-.-&gt;|&#34;matched by name&#34;|" in page  # a dashed arrow, escaped like all page text
    assert "Why each arrow is there" in page
    assert 'href="/source/apps/site/package.json#L1"' in page  # an explicit arrow cites its file and line
    assert "matched by name:" in page
    for heading in ("Services", "Apps", "Contracts", "Infrastructure", "Platforms"):
        assert f"<h3>{heading}</h3>" in page


def test_home_and_sidebar_lead_to_the_system(client: TestClient) -> None:
    page = client.get("/").text
    assert "Your system" in page
    assert page.count('href="/system"') >= 2  # the card and the sidebar


def test_an_area_shows_where_it_fits(client: TestClient) -> None:
    page = client.get("/areas/apps").text
    assert "Where it fits" in page
    assert page.count('<pre class="diagram">') >= 1


def test_a_repository_without_parts_has_no_system(tmp_path: Path) -> None:
    client = client_for(tmp_path, {"README.md": "# Notes\n"})
    assert client.get("/system").status_code == 404
    assert 'href="/system"' not in client.get("/").text
