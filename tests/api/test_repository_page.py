"""The Repository page, its home card and its sidebar link (design section 19.5)."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codetrail.config import GlobalConfig, MetricsSettings, Paths, write_target
from codetrail.errors import CodetrailError
from codetrail.repo.history import first_commit_time
from codetrail.repo.secrets import SecretScanner
from codetrail.update import run_update
from codetrail.web.app import create_app
from codetrail.web.security import SessionState
from tests.fixtures.repos import Commit, add_commit, fake_github_token, git, make_repository

ORIGIN = "http://127.0.0.1:8765"
FILES: Commit = {
    "pyproject.toml": '[project]\nname = "shop"\ndependencies = ["fastapi", "httpx"]\n',
    "app/main.py": "from app import db\nprint(db)\n",
    "app/db.py": "",
    "tests/test_main.py": "def test_x():\n    pass\n",
    "README.md": "# Shop\n",
    "private/hidden_module.py": "HIDDEN = 1\n",
}


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    checkout = make_repository(tmp_path / "target", [FILES])
    git(checkout, "tag", "-a", "v1.0", "-m", "Release one\n\nThe first notes.", date=1)
    add_commit(checkout, {"app/db.py": "x = 1\n"}, "feat(app): b\n\nWhy: it helps.")
    add_commit(checkout, {"app/db.py": "x = 2\n"}, "fix: c")
    write_target(paths, "shop", checkout, "develop")
    paths.ignore_file("shop").write_text("private/\n")
    return paths


class FixedDate(date):
    @classmethod
    def today(cls) -> FixedDate:
        return cls(2026, 10, 10)


def client_for(paths: Paths, settings: GlobalConfig | None = None) -> TestClient:
    session = SessionState(60)
    app = create_app(paths, "shop", session, settings or GlobalConfig())
    client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
    assert client.get(f"/login?code={session.issue_login_code()}").status_code == 303
    return client


def section(page: str, name: str) -> str:
    return page.split(f'id="{name}"', 1)[1].split("</section>", 1)[0]


def test_the_page_needs_the_session(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    app = create_app(paths, "shop", SessionState(60), GlobalConfig())
    assert TestClient(app, base_url=ORIGIN).get("/repository").status_code == 403


def test_before_any_facts_the_page_says_how_to_start(paths: Paths) -> None:
    page = client_for(paths).get("/repository")
    assert page.status_code == 200
    assert "No facts yet" in page.text and "data-update-button" in page.text


def test_the_page_shows_history_releases_size_and_facts(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/repository").text
    timeline = section(page, "timeline")
    assert "First commit" in timeline and "2026-09-21" in timeline  # the fixtures' first commit date
    assert "Commits per month" in timeline and "<rect" in timeline
    releases = section(page, "releases")
    assert "v1.0" in releases and "The first notes." in releases and "2 commits since" in releases
    size = section(page, "size")
    assert "Python" in size and "TOML" in size and "test_main.py" not in size.split("<h3", 1)[0]
    assert "hidden_module" not in page  # an excluded file is never read or listed
    types = section(page, "types")
    assert "feat" in types and "app" in types
    facts = section(page, "facts")
    assert "package" in facts
    assert "Test Author" not in page and "author@example.com" not in page  # about the repository, not people


def test_a_flagged_tag_is_withheld(paths: Paths, tmp_path: Path) -> None:
    token = fake_github_token()
    git(tmp_path / "target", "tag", "-a", "v2.0", "-m", f"Rotated {token}")
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/repository").text
    assert token not in page and "v2.0" not in page
    assert "withheld" in section(page, "releases")


def test_a_repository_without_tags_says_so(tmp_path: Path) -> None:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "shop", make_repository(tmp_path / "target", [FILES]), "develop")
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/repository").text
    assert "No releases" in section(page, "releases")
    assert "No earlier update to compare with" in page


def test_without_the_history_the_rest_still_shows(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    run_update(paths, "shop", facts_only=True)

    def broken(self: SecretScanner, text: str) -> object:
        raise CodetrailError("gitleaks isn't installed")

    monkeypatch.setattr(SecretScanner, "scan_text", broken)
    page = client_for(paths).get("/repository").text
    assert "The releases couldn't be read" in section(page, "releases")
    assert "Python" in section(page, "size")


def test_the_trend_and_the_change_since_the_last_update(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "shop", facts_only=True)
    add_commit(tmp_path / "target", {"app/extra.py": "a = 1\nb = 2\n"}, "feat: more")
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/repository").text
    assert "+1 since the last update" in page  # commits
    assert "+2 since the last update" in page  # lines of code
    assert "Trend over 2 updates" in page


def test_home_and_sidebar_lead_to_the_page(paths: Paths) -> None:
    client = client_for(paths)
    assert 'href="/repository"' in client.get("/decisions").text  # the sidebar, before any facts
    run_update(paths, "shop", facts_only=True)
    home = client.get("/").text
    assert 'aria-labelledby="repository-title"' in home
    assert "3 commits" in home
    assert home.count('href="/repository"') >= 2  # the card and the sidebar


def test_the_page_carries_the_same_security_headers(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    client = client_for(paths)
    page, decisions = client.get("/repository"), client.get("/decisions")
    for header in ("content-security-policy", "x-frame-options", "referrer-policy", "cache-control"):
        assert page.headers.get(header) == decisions.headers.get(header), header


def test_a_young_repository_shows_its_age_in_days(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("codetrail.web.app.date", FixedDate)
    run_update(paths, "shop", facts_only=True)
    tile = client_for(paths).get("/repository").text.split('id="tile-age"', 1)[1].split("</section>", 1)[0]
    assert "19 days" in tile and "month" not in tile  # first commit on 2026-09-21


def test_scopes_are_pills(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    assert '<li class="pill">app · 1</li>' in section(client_for(paths).get("/repository").text, "types")


def test_only_the_largest_files_are_listed(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths, GlobalConfig(metrics=MetricsSettings(largest_files=1))).get("/repository").text
    largest = section(page, "size").split("Largest files", 1)[1]
    assert largest.count('href="/source/') == 1 and "more" not in largest


class EarlyDate(date):
    @classmethod
    def today(cls) -> EarlyDate:
        return cls(2026, 9, 20)  # a clock a day behind the commits' dates, or a reader west of UTC


def test_days_ago_never_go_negative(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("codetrail.web.app.date", EarlyDate)
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths).get("/repository").text
    assert "-1 day" not in page
    assert "0 days ago" in section(page, "timeline")


def test_a_cut_history_says_so(paths: Paths) -> None:
    run_update(paths, "shop", facts_only=True)
    page = client_for(paths, GlobalConfig(metrics=MetricsSettings(history_limit=1))).get("/repository").text
    assert "The timeline shows the latest 1 commits" in section(page, "timeline")


def test_the_home_card_reads_the_first_commit_once_per_snapshot(paths: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def counted(mirror: Path, end: str) -> int | None:
        calls.append(end)
        return first_commit_time(mirror, end)

    monkeypatch.setattr("codetrail.web.app.first_commit_time", counted)
    run_update(paths, "shop", facts_only=True)
    client = client_for(paths)
    assert "Started on 2026-09-21" in client.get("/").text
    client.get("/")
    assert len(calls) == 1
