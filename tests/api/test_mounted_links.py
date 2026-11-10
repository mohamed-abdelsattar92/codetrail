"""One target's page mounted under /repos/<target>/, as the team page serves it (design section 24.7)."""

import json
import re
from collections.abc import Callable
from html.parser import HTMLParser
from importlib.resources import files
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from codetrail.assistant import AnswerChunk, Assistant, PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.lock import target_removal
from codetrail.update import run_update
from codetrail.web.app import create_app, create_target_app
from codetrail.web.security import TOKEN_HEADER, SecurityMiddleware, SessionState
from tests.fixtures.repos import MIXED_REPOSITORY, make_repository

ORIGIN = "http://127.0.0.1:8765"
PREFIX = "/repos/web-shop"
# Taken from the page before any link went through site_path (commit 9957dbf), at `/`.
UNMOUNTED_LINKS = Path(__file__).parent.parent / "fixtures" / "unmounted_links.json"
DIGEST_ID = re.compile(r"/digests/\d{4}-\d{2}-\d{2}-[0-9a-f]+")  # holds today's date
PLAN = PlanDraft(
    [{"id": "areas/apps", "kind": "area", "title": "The apps", "scope_paths": ["apps"], "facts": []}],
    paths=[{"id": "paths/start", "title": "Start", "goal": "Learn the apps.", "steps": ["areas/apps"]}],
)
PAGES = [
    "/",
    "/pages/areas/apps",
    "/pages/paths/start",
    "/areas/apps",
    "/facts/project:apps/site",
    "/source/apps/site/package.json",
    "/digests",
    "/progress",
    "/answers",
    "/system",
    "/repository",
    "/documentation",
    "/decisions",
    "/search?q=x",
    "/search?q=apps",
]


def page_writer(request: PageRequest) -> PageDraft:
    body = (
        "The apps hold [[project:apps/site]]; see [the area](/areas/apps).\n\n"
        '> [!documented] docs/adr/0001-x.md#L3-L3\n> "We chose Python."\n\n> [!inferred]\n> It reads well.\n\n'
        "{{diagram system}}\n"
    )
    checks = [{"id": "q", "question": "Why?", "rubric": [{"point": "p", "grounds": ["apps/site/package.json"]}]}]
    return PageDraft(body, checks, ["apps/site/package.json"], brief="The key file is apps/site/package.json.")


@pytest.fixture(scope="module")
def paths(tmp_path_factory: pytest.TempPathFactory) -> Paths:
    root = tmp_path_factory.mktemp("mounted")
    paths = Paths(config_dir=root / "config", data_dir=root / "data", state_dir=root / "state")
    adr = {"docs/adr/0001-x.md": "# 0001. X\n\nWe chose Python.\n"}
    checkout = make_repository(root / "target", [{**MIXED_REPOSITORY, **adr}])
    write_target(paths, "web-shop", checkout, "develop")
    run_update(paths, "web-shop", claude=FakeAssistant(plans=[PLAN], page_writer=page_writer))
    return paths


def mounted_client(
    paths: Paths, session: SessionState, assistant_for: Callable[[], Assistant] | None = None
) -> TestClient:
    """The target's page under its prefix and the shared static files at the root, behind one security middleware."""
    static = StaticFiles(directory=str(files("codetrail.web").joinpath("static")))
    target, _stop = create_target_app(paths, "web-shop", session, GlobalConfig(), assistant_for=assistant_for)
    app = Starlette(routes=[Mount(PREFIX, app=target), Mount("/static", app=static)])
    secured = SecurityMiddleware(app, session=session, port=8765, mounted_targets={"web-shop"})
    client = TestClient(secured, base_url=ORIGIN, follow_redirects=False)
    response = client.get(f"{PREFIX}/login?code={session.issue_login_code()}")
    assert response.status_code == 303
    assert response.headers["location"] == f"{PREFIX}/"
    return client


def unmounted_client(paths: Paths, session: SessionState) -> TestClient:
    client = TestClient(create_app(paths, "web-shop", session, GlobalConfig()), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


class Links(HTMLParser):
    """Every href, src and action, and every data-* attribute holding a root-relative URL."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name in ("href", "src", "action") or (name.startswith("data-") and value.startswith("/")):
                self.found.append(value)


def links_of(html: str) -> list[str]:
    parser = Links()
    parser.feed(html)
    # The repository page's chart document carries links for its script too.
    return parser.found + re.findall(r'"href": ?"([^"]+)"', html)


def crawl(client: TestClient, prefix: str) -> list[str]:
    found: list[str] = []
    for page in PAGES:
        response = client.get(prefix + page)
        assert response.status_code == 200, page
        found += links_of(response.text)
    results = client.get(f"{prefix}/search/results?q=apps", headers={TOKEN_HEADER: client_token(client, prefix)})
    found += [result["link"] for result in results.json()["results"]]
    return found


def client_token(client: TestClient, prefix: str) -> str:
    match = re.search(r'<meta name="codetrail-token" content="([^"]+)">', client.get(prefix + "/").text)
    assert match is not None
    return match.group(1)


def is_link_out(link: str) -> bool:
    return link.startswith(("http://", "https://")) and not link.startswith(ORIGIN)


def test_every_link_stays_under_the_prefix(paths: Paths) -> None:
    links = crawl(mounted_client(paths, SessionState(60)), PREFIX)
    assert any(link.startswith(f"{PREFIX}/pages/") for link in links)
    assert any(link.startswith(f"{PREFIX}/source/") for link in links)
    assert f"{PREFIX}/areas/apps" in links  # a link the guide's own Markdown holds
    stray = [
        link for link in links
        if not (link.startswith((f"{PREFIX}/", "/static/", "#")) or is_link_out(link))
    ]  # fmt: skip
    assert stray == []


def test_unmounted_links_are_unchanged(paths: Paths) -> None:
    unmounted = crawl(unmounted_client(paths, SessionState(60)), "")
    mounted = crawl(mounted_client(paths, SessionState(60)), PREFIX)
    assert not any(link.startswith("/repos/") for link in unmounted)
    under_prefix = [PREFIX + link if link.startswith("/") and not link.startswith("/static/") else link
                    for link in unmounted]  # fmt: skip
    assert under_prefix == mounted


def test_unmounted_links_match_the_page_before_the_change(paths: Paths) -> None:
    client = unmounted_client(paths, SessionState(60))
    before = json.loads(UNMOUNTED_LINKS.read_text())
    now = {
        page: sorted({DIGEST_ID.sub("/digests/<id>", link) for link in links_of(client.get(page).text)
                      if link.startswith("/")})
        for page in before
    }  # fmt: skip
    assert now == before


def test_the_meta_base_is_the_prefix(paths: Paths) -> None:
    mounted = mounted_client(paths, SessionState(60)).get(f"{PREFIX}/").text
    assert f'<meta name="codetrail-base" content="{PREFIX}">' in mounted
    unmounted = unmounted_client(paths, SessionState(60)).get("/").text
    assert '<meta name="codetrail-base" content="">' in unmounted


def test_the_page_s_reads_and_writes_work_under_the_prefix(paths: Paths) -> None:
    session = SessionState(60)
    client = mounted_client(paths, session)
    assert client.get(f"{PREFIX}/bridge/answers").status_code == 403
    assert client.get(f"{PREFIX}/bridge/answers", headers={TOKEN_HEADER: session.token}).json() == {"answers": []}
    write = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    unknown = {"language": "xx"}  # refused by the route once past the middleware, so the fixture keeps its settings
    assert client.post(f"{PREFIX}/settings/language", json=unknown).status_code == 403
    assert client.post(f"{PREFIX}/settings/language", json=unknown, headers=write).status_code == 400
    assert client.get("/static/js/api.js").status_code == 200


def test_a_mounted_page_holds_its_target_but_sign_in_and_static_files_don_t(paths: Paths) -> None:
    client = mounted_client(paths, SessionState(60))
    with target_removal(paths, "web-shop"):
        page = client.get(f"{PREFIX}/")
        login = client.get(f"{PREFIX}/login?code=wrong")
        own_static = client.get(f"{PREFIX}/static/css/base.css")
        shared_static = client.get("/static/css/base.css")
    assert page.status_code == 409
    assert "being removed" in page.text
    assert login.status_code == 403
    assert "removed" not in login.text
    assert own_static.status_code == shared_static.status_code == 200


def test_an_answer_s_links_carry_the_prefix_once(paths: Paths) -> None:
    body = "It is [[project:apps/site]], set up in [the manifest](/source/apps/site/package.json)."
    claude = FakeAssistant(answers=[[AnswerChunk(body), AnswerChunk(done=True)]])
    session = SessionState(60)
    client = mounted_client(paths, session, assistant_for=lambda: claude)
    headers = {"origin": ORIGIN, TOKEN_HEADER: session.token}
    response = client.post(f"{PREFIX}/bridge/questions", json={"question": "What is it?"}, headers=headers)
    done = json.loads(response.text.splitlines()[-1])
    listed = client.get(f"{PREFIX}/bridge/answers", headers={TOKEN_HEADER: session.token}).json()["answers"]
    for html in (done["html"], listed[0]["html"]):
        assert f'href="{PREFIX}/facts/project%3Aapps%2Fsite"' in html
        assert f'href="{PREFIX}/source/apps/site/package.json"' in html
        assert html.count(PREFIX) == 2
