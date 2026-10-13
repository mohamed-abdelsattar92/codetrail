"""The Documentation page, its home card and its sidebar link (design section 18.4)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.assistant import PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import GlobalConfig, Paths, write_target
from codetrail.errors import CodetrailError
from codetrail.repo.secrets import SecretScanner
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.fixtures.repos import Commit, add_commit, fake_github_token, make_repository

ORIGIN = "http://127.0.0.1:8765"
FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi", "httpx"]\n',
    "app/main.py": "from app import db\n",
    "app/db.py": "",
    "README.md": "# Shop\n\nBuilt with FastAPI.\n",
    "docs/adr/0001-x.md": "# 0001. X\n\nStatus: accepted\n\nWe chose Python.\n",
    "docs/adr/0002-y.md": "# 0002. Y\n\nStatus: proposed\nDate: 2020-01-01\n\nMaybe Rust.\n",
    "private/notes.md": "HIDDEN-NOTE-TEXT: we use httpx because it is async.\n",
}
PLAN = PlanDraft([{"id": "areas/app", "kind": "area", "title": "The app", "scope_paths": ["app"], "facts": []}])


def page_writer(request: PageRequest) -> PageDraft:
    body = '> [!documented] docs/adr/0001-x.md#L5-L5\n> "We chose Python."\n\n> [!inferred]\n> It reads well.\n'
    checks = [{"id": "q", "question": "Why?", "rubric": [{"point": "p", "grounds": ["app/main.py"]}]}]
    return PageDraft(body, checks, ["app/main.py"])


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "target", [FILES])
    add_commit(checkout, {"app/db.py": "x = 1\n"}, "feat: b\n\nWhy: it helps.")
    add_commit(checkout, {"app/db.py": "x = 2\n"}, "fix: c\n\nJust a body.")
    write_target(paths, "shop", checkout, "develop")
    paths.ignore_file("shop").write_text("private/\n")
    return paths


def client_for(paths: Paths) -> TestClient:
    session = SessionState(60)
    client = TestClient(create_app(paths, "shop", session, GlobalConfig()), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def test_the_page_needs_the_session(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    app = create_app(paths, "shop", SessionState(60), GlobalConfig())
    assert TestClient(app, base_url=ORIGIN).get("/documentation").status_code == 403


def test_before_any_facts_the_page_says_how_to_start(paths: Paths) -> None:
    page = client_for(paths).get("/documentation")
    assert page.status_code == 200
    assert "No facts yet" in page.text


def test_before_the_first_paid_update_everything_but_the_rationale_is_measured(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/documentation").text
    assert "No pages yet" in page
    assert "data-update-button" in page  # opens the estimate dialog; nothing is spent from here
    assert "1 of 3" in page  # commits that explain why
    assert "0002" in page and "Y" in page  # the proposed ADR, waiting since 2020
    assert 'href="/facts/package%3Apypi/httpx"' in page  # no visible document names it
    assert "HIDDEN-NOTE-TEXT" not in page  # an excluded file is never read
    assert "Test Author" not in page  # metrics are about the repository, not people
    assert "measured once your assistant has written its pages" in page
    assert 'href="/facts/decision%3AADR-0001"' not in page  # with no pages, every ADR would be "uncited"
    assert 'href="/facts/module%3Aapp/db.py"' not in page  # and every fact "unexplained"


def test_after_a_paid_update_the_rationale_is_measured(paths: Paths) -> None:
    run_update(paths, "shop", claude=FakeAssistant(plans=[PLAN], page_writer=page_writer))
    page = client_for(paths).get("/documentation").text
    assert "1 of 2" in page  # documented rationale
    assert 'href="/pages/areas/app"' in page and "It reads well." in page
    assert "fix: c" in page and "Commit 0" in page  # commits without a why
    assert "feat: b" not in page.split('id="commits"', 1)[1].split("</section>", 1)[0]


def test_a_flagged_commit_message_is_withheld(paths: Paths, tmp_path: Path) -> None:
    token = fake_github_token()
    add_commit(tmp_path / "target", {"app/db.py": "x = 3\n"}, f"fix: rotate\n\nThe old one was {token}")
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/documentation").text
    assert token not in page
    assert "1 message withheld" in page


def test_without_the_history_the_other_metrics_still_show(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    run_update(paths, "shop", facts_only=True)

    def broken(self: SecretScanner, text: str) -> object:
        raise CodetrailError("gitleaks isn't installed")

    monkeypatch.setattr(SecretScanner, "scan_text", broken)
    page = client_for(paths).get("/documentation").text
    assert "The commit history couldn't be read" in page
    assert 'href="/facts/package%3Apypi/httpx"' in page


def test_the_trend_and_the_change_since_the_last_update(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/documentation").text
    assert "No earlier update to compare with" in page
    assert "Trend over 1 update: 33%" in page
    add_commit(tmp_path / "target", {"app/db.py": "x = 4\n"}, "perf: d\n\nWhy: faster.")
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/documentation").text
    assert "+17 points since the last update" in page  # 1 of 3, then 2 of 4
    assert "Trend over 2 updates: 33%, 50%" in page


def test_home_and_sidebar_lead_to_the_page(paths: Paths) -> None:
    client = client_for(paths)
    assert 'href="/documentation"' in client.get("/decisions").text  # the sidebar, before any facts
    run_update(paths, "shop", facts_only=True)
    home = client.get("/").text
    assert 'aria-labelledby="documentation-title"' in home
    assert home.count('href="/documentation"') >= 2  # the card and the sidebar


def test_the_page_carries_the_same_security_headers(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    client = client_for(paths)
    page, decisions = client.get("/documentation"), client.get("/decisions")
    for header in ("content-security-policy", "x-frame-options", "referrer-policy", "cache-control"):
        assert page.headers.get(header) == decisions.headers.get(header), header


def section(page: str, name: str) -> str:
    """One metric's section of the page, from its id to its end."""
    return page.split(f'<section id="{name}"', 1)[1].split("</section>", 1)[0]


def client_listing(paths: Paths, listed: int) -> TestClient:
    session = SessionState(60)
    settings = GlobalConfig.model_validate({"metrics": {"max_listed": listed}})
    client = TestClient(create_app(paths, "shop", session, settings), base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def test_the_section_most_in_need_opens_and_no_other(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)  # ADR 0002 has been proposed since 2020
    page = client_for(paths).get("/documentation").text
    assert page.count('<details class="doc-fold" open>') == 1
    assert '<details class="doc-fold" open>' in section(page, "decisions")
    assert 'class="doc-overview"' in page


def test_each_chart_prints_its_numbers(paths: Paths) -> None:
    run_update(paths, "shop", claude=FakeAssistant(plans=[PLAN], page_writer=page_writer))
    page = client_for(paths).get("/documentation").text
    rationale, commits = section(page, "rationale"), section(page, "commits")
    assert 'aria-label="ADRs: 1, Documents: 0, Code comments: 0, Commit messages: 0, Inferred: 1"' in rationale
    assert 'href="/pages/areas/app"' in rationale and "1 / 1" in rationale  # the page's bar: documented / inferred
    assert 'aria-label="Explain why: 1, Body without a why: 1, Subject only: 1"' in commits
    decisions = section(page, "decisions")
    assert ">accepted<" in decisions and ">proposed<" in decisions
    assert "Explained by a guide page" in section(page, "coverage")


def test_every_item_carries_its_kind_and_the_chips_start_hidden(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/documentation").text
    commits, coverage = section(page, "commits"), section(page, "coverage")
    assert 'data-kind="body-only"' in commits and 'data-kind="subject-only"' in commits
    assert "data-filter hidden" in commits
    assert 'data-show="body-only" aria-pressed="false"' in commits
    assert 'data-kind="package"' in coverage
    assert "data-filter hidden" not in coverage  # only packages go unmentioned: one kind needs no chips


def test_a_long_list_counts_its_folded_rest_per_kind(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    commits = section(client_listing(paths, 1).get("/documentation").text, "commits")
    # Newest first: "fix: c" (a body, no why) shows; "Commit 0" (a subject only) folds away.
    assert '<span data-more="all" data-count="1">1 more</span>' in commits
    assert '<span data-more="body-only" data-count="0" hidden>' in commits
    assert '<span data-more="subject-only" data-count="1" hidden>' in commits


def test_markup_in_a_commit_subject_stays_text(paths: Paths, tmp_path: Path) -> None:
    add_commit(tmp_path / "target", {"app/db.py": "x = 5\n"}, "fix: close </details><b>bold</b>")
    run_update(paths, "shop", facts_only=True)
    commits = section(client_for(paths).get("/documentation").text, "commits")
    assert "&lt;/details&gt;&lt;b&gt;bold&lt;/b&gt;" in commits
    assert "<b>bold</b>" not in commits


def test_without_the_history_the_commits_draw_nothing(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    run_update(paths, "shop", facts_only=True)

    def broken(self: SecretScanner, text: str) -> object:
        raise CodetrailError("gitleaks isn't installed")

    monkeypatch.setattr(SecretScanner, "scan_text", broken)
    commits = section(client_for(paths).get("/documentation").text, "commits")
    assert "stack-bar" not in commits and "data-filterable" not in commits
