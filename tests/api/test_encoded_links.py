"""Links built from folder and file names are URL-encoded, so a `#` or `?` in a name can't cut or split the link."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.config import DiagramSettings, GlobalConfig, Paths, write_target
from codetrail.guide import GuideRepository, Page
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.fixtures.repos import add_commit, make_repository

ORIGIN = "http://127.0.0.1:8765"
ODD_PAGE = """See [[module:svc#1/app/main.py]].

> [!documented] a?b/tool.py#L1
> "x = 1"

{{diagram imports scope=svc#1/lib}}

{{diagram resources scope=infra#1}}
"""


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(
        tmp_path / "target",
        [
            {
                "docs#1/notes.md": "# Notes\n",
                "a?b/tool.py": "x = 1\n",
                "svc#1/pyproject.toml": '[project]\nname = "api"\ndependencies = ["httpx"]\n',
                "svc#1/app/__init__.py": "",
                "svc#1/app/main.py": "print(1)\n",
                "svc#1/lib/pyproject.toml": '[project]\nname = "lib"\n',
                "svc#1/lib/core.py": "y = 2\n",
                "infra#1/main.tf": 'resource "null_resource" "x" {}\n',
                "docs/adr/0002-use-c#.md": "# 0002. Use C#\n\n- Status: accepted\n",
            }
        ],
    )
    write_target(paths, "t", checkout, "develop")
    run_update(paths, "t", facts_only=True)
    package = '{"name": "web", "dependencies": {"left#pad": "1.0.0"}}\n'
    add_commit(checkout, {"svc#1/app/main.py": "print(2)\n", "web/package.json": package}, "fix: c")
    run_update(paths, "t", facts_only=True)
    GuideRepository(paths.target_data("t") / "guide").write_page(
        Page("concepts/odd-names", {"kind": "concept", "title": "Odd names"}, ODD_PAGE)
    )
    session = SessionState(60)
    config = GlobalConfig(diagrams=DiagramSettings(max_nodes=1))  # small enough to roll the diagrams up to folders
    client = TestClient(create_app(paths, "t", session, config), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def opens(client: TestClient, href: str, expected: str) -> None:
    response = client.get(href)
    assert response.status_code == 200
    assert expected in response.text


def diagrams(page: str) -> str:
    return "".join(figure.split("</figure>", 1)[0] for figure in page.split('<figure class="diagram-block"')[1:])


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


def test_a_rolled_up_imports_diagram_links_each_folder_by_its_encoded_path(client: TestClient) -> None:
    assert 'href="/areas/svc%231/app"' in diagrams(client.get("/areas/svc%231/app").text)
    opens(client, "/areas/svc%231/app", "svc#1/app/main.py")


def test_a_rolled_up_system_diagram_links_each_folder_by_its_encoded_path(client: TestClient) -> None:
    drawn = diagrams(client.get("/areas/svc%231").text)
    assert drawn.count('href="/areas/svc%231"') == 2  # its two parts in the system diagram, its modules in the imports


def test_diagrams_link_facts_by_their_encoded_ids(client: TestClient) -> None:
    area = diagrams(client.get("/areas/svc%231/lib").text)
    assert 'href="/facts/part%3Asvc%231/lib"' in area  # the system diagram, focused on one part
    assert 'href="/facts/project%3Asvc%231/lib"' in area  # its dependencies
    opens(client, "/facts/part%3Asvc%231/lib", "part:svc#1/lib")
    assert 'href="/facts/project%3Asvc%231"' in diagrams(client.get("/areas/svc%231").text)
    page = diagrams(client.get("/pages/concepts/odd-names").text)
    assert 'href="/facts/module%3Asvc%231/lib/core.py"' in page  # the imports diagram, one module
    assert 'href="/facts/terraform_module%3Ainfra%231"' in page  # the resources diagram
    opens(client, "/facts/module%3Asvc%231/lib/core.py", "module:svc#1/lib/core.py")


def test_a_guide_page_links_facts_and_citations_by_their_encoded_paths(client: TestClient) -> None:
    page = client.get("/pages/concepts/odd-names").text
    assert 'href="/facts/module%3Asvc%231/app/main.py"' in page
    assert 'href="/source/a%3Fb/tool.py#L1"' in page
    opens(client, "/facts/module%3Asvc%231/app/main.py", "module:svc#1/app/main.py")
    opens(client, "/source/a%3Fb/tool.py", "x = 1")


def test_search_links_facts_by_their_encoded_ids(client: TestClient) -> None:
    assert 'href="/facts/module%3Asvc%231/app/main.py"' in client.get("/search", params={"q": "main"}).text


def test_no_link_holds_a_raw_hash_or_question_mark(client: TestClient) -> None:
    for page in ("/system", "/areas/svc%231", "/areas/svc%231/lib", "/facts/project%3Asvc%231", "/facts/project%3Aweb",
                 "/documentation", "/repository", "/pages/concepts/odd-names", "/search?q=svc"):  # fmt: skip
        links = re.findall(r'href="(/(?:facts|areas|source)/[^"]*)"', client.get(page).text)
        assert links, page
        assert [link for link in links if "?" in link or "#" in re.sub(r"#L\d+$", "", link)] == [], page


def test_a_package_that_arrived_links_its_fact_by_its_encoded_id(client: TestClient) -> None:
    assert 'href="/facts/package%3Anpm/left%23pad"' in client.get("/repository").text
    assert 'href="/facts/package%3Anpm/left%23pad"' in client.get("/facts/project%3Aweb").text
    opens(client, "/facts/package%3Anpm/left%23pad", "package:npm/left#pad")
