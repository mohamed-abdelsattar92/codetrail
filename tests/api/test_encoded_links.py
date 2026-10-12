"""Links built from folder and file names are URL-encoded, so a `#` or `?` in a name can't cut or split the link."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.fixtures.repos import add_commit, make_repository

ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(
        tmp_path / "target",
        [
            {
                "docs#1/notes.md": "# Notes\n",
                "a?b/tool.py": "x = 1\n",
                "svc#1/pyproject.toml": '[project]\nname = "api"\n',
                "svc#1/app/__init__.py": "",
                "svc#1/app/main.py": "print(1)\n",
                "docs/adr/0002-use-c#.md": "# 0002. Use C#\n\n- Status: accepted\n",
            }
        ],
    )
    add_commit(checkout, {"svc#1/app/main.py": "print(2)\n"}, "fix: c")
    write_target(paths, "t", checkout, "develop")
    run_update(paths, "t", facts_only=True)
    session = SessionState(60)
    client = TestClient(create_app(paths, "t", session, GlobalConfig()), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def opens(client: TestClient, href: str, expected: str) -> None:
    response = client.get(href)
    assert response.status_code == 200
    assert expected in response.text


@pytest.mark.parametrize(("folder", "href", "file"), [("docs#1", "/areas/docs%231", "notes.md"),
                                                      ("a?b", "/areas/a%3Fb", "tool.py")])  # fmt: skip
def test_home_links_each_folder_by_its_encoded_path(client: TestClient, folder: str, href: str, file: str) -> None:
    assert f'href="{href}"' in client.get("/").text
    opens(client, href, f"{folder}/{file}")


def test_an_area_links_its_files_by_their_encoded_paths(client: TestClient) -> None:
    assert 'href="/source/docs%231/notes.md"' in client.get("/areas/docs%231").text
    opens(client, "/source/docs%231/notes.md", "# Notes")


def test_a_fact_links_its_sources_by_their_encoded_paths(client: TestClient) -> None:
    assert 'href="/source/svc%231/app/main.py"' in client.get("/facts/module:svc%231/app/main.py").text


def test_decisions_link_their_files_by_their_encoded_paths(client: TestClient) -> None:
    assert 'href="/source/docs/adr/0002-use-c%23.md"' in client.get("/decisions").text
    opens(client, "/source/docs/adr/0002-use-c%23.md", "Use C#")


def test_the_system_links_each_part_to_its_encoded_folder(client: TestClient) -> None:
    assert 'href="/areas/svc%231"' in client.get("/system").text


def test_the_repository_page_links_files_by_their_encoded_paths(client: TestClient) -> None:
    page = client.get("/repository").text
    assert page.count('href="/source/svc%231/app/main.py"') == 2  # the most changed files and the largest files
