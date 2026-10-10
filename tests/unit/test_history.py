"""Filtered history: logs and diffs never show excluded paths or secrets (design section 3.3)."""

import os
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from codetrail.config import ToolsSettings
from codetrail.errors import CodetrailError
from codetrail.repo.history import (
    WITHHELD_TAG,
    changed_files,
    changed_paths,
    commit_count,
    commit_times,
    commit_totals,
    commits_between,
    diff_between,
    files_changed_since,
    first_commit_time,
    latest_commits,
    merges_between,
    read_tags,
    recent_commits,
)
from codetrail.repo.mirror import refresh_mirror
from codetrail.repo.rules import ExclusionRules
from codetrail.repo.secrets import SecretScanner
from tests.fixtures.repos import GIT_ENVIRONMENT, add_commit, fake_github_token, git, make_repository, write_commit

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


def test_latest_commits_skip_merges_and_withhold_secrets(tmp_path: Path, scanner: SecretScanner) -> None:
    token = fake_github_token()
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    add_commit(checkout, {"b.md": "b\n"}, "feat: b\n\nWhy: because\nit helps.")
    add_commit(checkout, {"c.md": "c\n"}, f"fix: c\n\nThe old one was {token}")
    git(checkout, "switch", "-q", "-c", "side")
    add_commit(checkout, {"d.md": "d\n"}, "feat: d")
    git(checkout, "switch", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    mirror, end = mirror_of(checkout, tmp_path)
    commits = latest_commits(mirror, end, 10, scanner)
    assert [commit.subject for commit in commits] == [
        "feat: d",
        "[withheld: gitleaks flagged this message]",
        "feat: b",
        "Commit 0",
    ]
    assert commits[2].body == "Why: because\nit helps."
    assert token not in repr(commits)
    assert len(latest_commits(mirror, end, 2, scanner)) == 2


def test_tags_are_read_with_their_messages_and_dates(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    first = git(checkout, "rev-parse", "HEAD")
    git(checkout, "tag", "light")
    second = add_commit(checkout, {"a.txt": "b\n"})
    git(checkout, "tag", "-a", "v1", "-m", "Release one\n\nThe notes.", date=9)
    git(checkout, "tag", "-a", "secret", "-m", f"token {fake_github_token()}")
    git(checkout, "tag", "on-a-blob", git(checkout, "rev-parse", "HEAD:a.txt"))
    git(checkout, "tag", "-a", "on-a-tree", "-m", "A tree", git(checkout, "rev-parse", "HEAD^{tree}"))
    mirror, end = mirror_of(checkout, tmp_path)
    tags = {tag.name: tag for tag in read_tags(mirror, end, scanner)}
    assert set(tags) == {"light", "v1", WITHHELD_TAG}
    assert (tags["light"].commit, tags["light"].message) == (first, "")
    assert (tags["v1"].commit, tags["v1"].message) == (second, "Release one\n\nThe notes.")
    assert tags["v1"].date == 1_790_000_000 + 9 * 60
    assert (tags[WITHHELD_TAG].commit, tags[WITHHELD_TAG].message) == (second, "")


def test_only_tags_in_the_commits_history_are_read(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    first = git(checkout, "rev-parse", "HEAD")
    git(checkout, "tag", "v1")
    add_commit(checkout, {"a.txt": "b\n"})
    git(checkout, "tag", "v2")
    mirror, end = mirror_of(checkout, tmp_path)
    assert sorted(tag.name for tag in read_tags(mirror, end, scanner)) == ["v1", "v2"]
    assert [tag.name for tag in read_tags(mirror, first, scanner)] == ["v1"]


def test_commit_totals_and_times_count_the_whole_history(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a": "1"}, {"a": "2"}])
    git(checkout, "checkout", "-q", "-b", "side")
    add_commit(checkout, {"b": "1"})
    git(checkout, "checkout", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "side", "-m", "Merge side", date=5)
    mirror, end = mirror_of(checkout, tmp_path)
    assert commit_totals(mirror, end) == (4, 1)
    times = commit_times(mirror, end, 10)
    assert sorted(times, reverse=True) == times
    assert times[0] == 1_790_000_000 + 5 * 60 and len(times) == 4
    assert commit_times(mirror, end, 2) == times[:2]


def test_the_first_commit_time_is_the_earliest_root_commit(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a": "1"}, {"a": "2"}])
    git(checkout, "checkout", "-q", "--orphan", "imported")
    git(checkout, "commit", "-q", "--allow-empty", "-m", "Imported", date=9)
    git(checkout, "checkout", "-q", "develop")
    git(checkout, "merge", "-q", "--allow-unrelated-histories", "imported", "-m", "Merge", date=10)
    mirror, end = mirror_of(checkout, tmp_path)
    assert first_commit_time(mirror, end) == 1_790_000_000


def test_a_tag_without_a_tagger_takes_its_commits_date(tmp_path: Path, scanner: SecretScanner) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.txt": "a\n"}])
    commit = git(checkout, "rev-parse", "HEAD")
    content = f"object {commit}\ntype commit\ntag ancient\n\nAn early release\n"  # tags made before git had taggers
    tag = subprocess.run(["git", "-C", str(checkout), "hash-object", "-t", "tag", "--literally", "-w", "--stdin"],
                         input=content, env={**os.environ, **GIT_ENVIRONMENT}, capture_output=True, text=True,
                         check=True).stdout.strip()  # fmt: skip
    git(checkout, "update-ref", "refs/tags/ancient", tag)
    mirror, end = mirror_of(checkout, tmp_path)
    [found] = read_tags(mirror, end, scanner)
    assert (found.name, found.commit, found.message) == ("ancient", commit, "An early release")
    assert found.date == 1_790_000_000  # the commit's date


SCANNER = SecretScanner(ToolsSettings())
HistoryReader = Callable[[Path, str, str], object]
READERS: dict[str, HistoryReader] = {
    "commits_between from": lambda mirror, option, end: commits_between(mirror, option, end, visible, SCANNER),
    "commits_between to": lambda mirror, option, end: commits_between(mirror, None, option, visible, SCANNER),
    "recent_commits": lambda mirror, option, end: recent_commits(mirror, option, ["a.md"], 5, visible, SCANNER),
    "latest_commits": lambda mirror, option, end: latest_commits(mirror, option, 5, SCANNER),
    "commit_count": lambda mirror, option, end: commit_count(mirror, option, end),
    "merges_between": lambda mirror, option, end: merges_between(mirror, option, end),
    "diff_between from": lambda mirror, option, end: diff_between(mirror, option, end, visible, SCANNER),
    "diff_between to": lambda mirror, option, end: diff_between(mirror, end, option, visible, SCANNER),
    "changed_paths from": lambda mirror, option, end: changed_paths(mirror, option, end),
    "changed_paths to": lambda mirror, option, end: changed_paths(mirror, end, option),
    "commit_totals": lambda mirror, option, end: commit_totals(mirror, option),
    "commit_times": lambda mirror, option, end: commit_times(mirror, option, 5),
    "first_commit_time": lambda mirror, option, end: first_commit_time(mirror, option),
    "read_tags": lambda mirror, option, end: read_tags(mirror, option, SCANNER),
    "changed_files": lambda mirror, option, end: changed_files(mirror, option, 5),
    "files_changed_since": lambda mirror, option, end: files_changed_since(mirror, option, date(2026, 1, 1)),
}


@pytest.mark.parametrize("reader", READERS.values(), ids=READERS.keys())
def test_a_revision_starting_with_a_dash_is_never_read_as_an_option(tmp_path: Path, reader: HistoryReader) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}, {"a.md": "b\n"}])
    mirror, end = mirror_of(checkout, tmp_path)
    written = tmp_path / "written"
    written.mkdir()
    with pytest.raises(CodetrailError):
        reader(mirror, f"--output={written / 'out'}", end)
    assert list(written.iterdir()) == []


DIFF_READERS: dict[str, Callable[[Path, str, str], object]] = {
    "diff_between": lambda mirror, start, end: diff_between(mirror, start, end, visible, SCANNER),
    "changed_paths": changed_paths,
}


@pytest.mark.parametrize("reader", DIFF_READERS.values(), ids=DIFF_READERS.keys())
def test_files_outside_the_repository_are_never_compared_as_revisions(
    tmp_path: Path, reader: Callable[[Path, str, str], object]
) -> None:
    checkout = make_repository(tmp_path / "t", [{"a.md": "a\n"}])
    mirror, _end = mirror_of(checkout, tmp_path)
    (tmp_path / "before").write_text("one\n")
    (tmp_path / "after").write_text("one\n")  # alike, so a file comparison would succeed
    with pytest.raises(CodetrailError):
        reader(mirror, str(tmp_path / "before"), str(tmp_path / "after"))


def test_changed_files_lists_each_latest_commits_paths(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"a b/x.txt": "1\n", "y": "1\n"}, {"y": "2\n"}])
    git(checkout, "commit", "-q", "--allow-empty", "-m", "Nothing", date=2)
    git(checkout, "checkout", "-q", "-b", "side")
    add_commit(checkout, {"z": "1\n"})
    git(checkout, "checkout", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "side", "-m", "Merge side", date=9)
    mirror, end = mirror_of(checkout, tmp_path)
    assert changed_files(mirror, end, 10) == [["z"], [], ["y"], ["a b/x.txt", "y"]]
    assert changed_files(mirror, end, 2) == [["z"], []]


def test_files_changed_since_a_date(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"old": "1\n"}])
    write_commit(checkout, {"new": "1\n"}, "Later", date=60 * 24 * 40)  # 40 days after 2026-09-21
    mirror, end = mirror_of(checkout, tmp_path)
    assert files_changed_since(mirror, end, date(2026, 10, 1)) == {"new"}
    assert files_changed_since(mirror, end, date(2026, 9, 1)) == {"old", "new"}  # before the first commit: all


def test_files_changed_since_keep_a_leading_newline(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "t", [{"old": "1\n"}])
    write_commit(checkout, {"\nlead": "1\n"}, "Later", date=60 * 24 * 40)
    mirror, end = mirror_of(checkout, tmp_path)
    assert files_changed_since(mirror, end, date(2026, 10, 1)) == {"\nlead"}
