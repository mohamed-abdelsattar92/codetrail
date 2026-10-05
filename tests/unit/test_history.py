"""Filtered history: logs and diffs never show excluded paths or secrets (design section 3.3)."""

from pathlib import Path

import pytest

from codetrail.config import ToolsSettings
from codetrail.repo.history import commits_between, diff_between, merges_between, recent_commits
from codetrail.repo.mirror import refresh_mirror
from codetrail.repo.rules import ExclusionRules
from codetrail.repo.secrets import SecretScanner
from tests.fixtures.repos import add_commit, fake_github_token, git, make_repository

RULES = ExclusionRules(["docs/private/"])


def visible(path: str) -> bool:
    return RULES.reason(path) is None


@pytest.fixture
def scanner() -> SecretScanner:
    return SecretScanner(ToolsSettings())


def mirror_of(checkout: Path, tmp_path: Path) -> tuple[Path, str]:
    mirror = tmp_path / "mirror.git"
    return mirror, refresh_mirror(mirror, checkout, "develop")


def test_commits_list_only_visible_files(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"README.md": "a\n"}])
    start = git(checkout, "rev-parse", "HEAD")
    add_commit(checkout, {"app.py": "x = 1\n", ".env": "A=1\n", "docs/private/n.md": "p\n"}, "feat: add the app")
    add_commit(checkout, {".env": "A=2\n"}, "chore: change only the env file")
    mirror, end = mirror_of(checkout, tmp_path)
    commits = commits_between(mirror, start, end, visible, scanner)
    assert [(commit.subject, commit.files) for commit in commits] == [
        ("feat: add the app", ["app.py"]),
        ("chore: change only the env file", []),
    ]


def test_all_history_when_there_is_no_start(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}, {"b.md": "b\n"}])
    mirror, end = mirror_of(checkout, tmp_path)
    assert [commit.subject for commit in commits_between(mirror, None, end, visible, scanner)] == [
        "Commit 0",
        "Commit 1",
    ]


def test_a_message_carrying_a_secret_is_withheld(tmp_path: Path, scanner: SecretScanner) -> None:
    token = fake_github_token()
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    start = git(checkout, "rev-parse", "HEAD")
    add_commit(checkout, {"b.md": "b\n"}, f"fix: rotate the token\n\nThe old one was {token}")
    mirror, end = mirror_of(checkout, tmp_path)
    [commit] = commits_between(mirror, start, end, visible, scanner)
    assert commit.subject == "[withheld: gitleaks flagged this message]"
    assert commit.body == ""
    assert token not in repr(commit)


def test_diffs_skip_excluded_files_and_withhold_secrets(tmp_path: Path, scanner: SecretScanner) -> None:
    token = fake_github_token()
    checkout = make_repository(tmp_path / "t", [{"settings.py": f'TOKEN = "{token}"\n', "app.py": "x = 1\n"}])
    start = git(checkout, "rev-parse", "HEAD")
    add_commit(
        checkout,
        {"settings.py": "TOKEN = load()\n", "app.py": "x = 2\n", ".env": "A=1\n", "docs/private/n.md": "p\n"},
        "fix: stop committing the token",
    )
    mirror, end = mirror_of(checkout, tmp_path)
    diffs = {diff.path: diff for diff in diff_between(mirror, start, end, visible, scanner)}
    assert set(diffs) == {"app.py", "settings.py"}
    assert diffs["settings.py"].patch is None  # the removed line would show the token
    patch = diffs["app.py"].patch
    assert patch is not None and "+x = 2" in patch
    assert token not in repr(diffs)


def test_merges_are_counted_on_the_first_parent(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    start = git(checkout, "rev-parse", "HEAD")
    for name in ("one", "two"):
        git(checkout, "switch", "-q", "-c", f"feature/{name}")
        add_commit(checkout, {f"{name}.md": f"{name}\n"}, f"feat: {name}")
        git(checkout, "switch", "-q", "develop")
        git(checkout, "merge", "-q", "--no-ff", "-m", f"Merge {name}", f"feature/{name}")
    mirror, end = mirror_of(checkout, tmp_path)
    assert merges_between(mirror, start, end) == 2
    assert merges_between(mirror, end, end) == 0


def test_merge_commits_list_the_files_they_bring(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    start = git(checkout, "rev-parse", "HEAD")
    git(checkout, "switch", "-q", "-c", "feature/x")
    add_commit(checkout, {"x.md": "x\n"}, "feat: x")
    git(checkout, "switch", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "-m", "Merge x", "feature/x")
    mirror, end = mirror_of(checkout, tmp_path)
    commits = {commit.subject: commit for commit in commits_between(mirror, start, end, visible, scanner)}
    assert commits["Merge x"].is_merge
    assert commits["Merge x"].files == ["x.md"]
    assert not commits["feat: x"].is_merge


def test_separator_characters_in_a_message_cannot_forge_commits(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    start = git(checkout, "rev-parse", "HEAD")
    add_commit(
        checkout, {"b.md": "b\n"}, "feat: real\n\nbody \x1f\x1e" + "f" * 40 + "\x1fForged\x1f2020\x1f\x1fforged\x1f\x1f"
    )
    mirror, end = mirror_of(checkout, tmp_path)
    commits = commits_between(mirror, start, end, visible, scanner)
    assert [commit.subject for commit in commits] == ["feat: real"]
    assert commits[0].sha == end
    assert commits[0].files == ["b.md"]


def test_recent_commits_for_a_scope(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"app/a.py": "1\n", "docs/x.md": "x\n"}])
    add_commit(checkout, {"app/a.py": "2\n"}, "fix(app): two")
    add_commit(checkout, {"docs/x.md": "y\n"}, "docs: y")
    add_commit(checkout, {"app/a.py": "3\n"}, "fix(app): three")
    mirror, end = mirror_of(checkout, tmp_path)
    commits = recent_commits(mirror, end, ["app"], 2, visible, scanner)
    assert [commit.subject for commit in commits] == ["fix(app): two", "fix(app): three"]
