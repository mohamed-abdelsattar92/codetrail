"""The mirror: a bare clone of the target, fetched over file:// so the target is only read (design section 3.2)."""

from pathlib import Path

import pytest

from codetrail.errors import CodetrailError
from codetrail.repo.git import run_git
from codetrail.repo.mirror import check_branch, list_tree, read_file_at, refresh_mirror, repository_url
from tests.fixtures.repos import Symlink, add_commit, git, make_repository, snapshot_tree


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    return make_repository(
        tmp_path / "target",
        [{"README.md": "# Hello\n", "app/main.py": "print('hi')\n", "link": Symlink("README.md")}],
    )


def test_the_url_uses_the_file_transport(checkout: Path) -> None:
    assert repository_url(checkout) == checkout.resolve().as_uri()


def test_first_refresh_clones_and_returns_the_head(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "data" / "mirror.git"
    commit = refresh_mirror(mirror, checkout, "develop")
    assert len(commit) == 40
    assert (mirror / "HEAD").exists()
    assert not (mirror / "objects" / "info" / "alternates").exists()


def test_later_refreshes_fetch_new_commits(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.git"
    first = refresh_mirror(mirror, checkout, "develop")
    second_commit = add_commit(checkout, {"app/extra.py": "x = 1\n"})
    second = refresh_mirror(mirror, checkout, "develop")
    assert second != first
    assert second == second_commit


def test_a_missing_branch_is_an_error(checkout: Path, tmp_path: Path) -> None:
    with pytest.raises(CodetrailError, match="main"):
        check_branch(checkout, "main")
    with pytest.raises(CodetrailError, match="main"):
        refresh_mirror(tmp_path / "mirror.git", checkout, "main")


def test_check_branch_accepts_an_existing_branch(checkout: Path) -> None:
    check_branch(checkout, "develop")


def test_the_target_is_never_changed(checkout: Path, tmp_path: Path) -> None:
    before = snapshot_tree(checkout)
    mirror = tmp_path / "mirror.git"
    check_branch(checkout, "develop")
    refresh_mirror(mirror, checkout, "develop")
    refresh_mirror(mirror, checkout, "develop")
    assert snapshot_tree(checkout) == before


def test_the_tree_lists_modes_blobs_and_paths(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.git"
    commit = refresh_mirror(mirror, checkout, "develop")
    entries = {entry.path: entry for entry in list_tree(mirror, commit)}
    assert set(entries) == {"README.md", "app/main.py", "link"}
    assert entries["link"].mode == "120000"
    assert entries["README.md"].mode == "100644"
    assert read_file_at(mirror, commit, "README.md") == b"# Hello\n"
    assert read_file_at(mirror, commit, "missing.txt") is None


def tags_in(mirror: Path) -> list[str]:
    names = run_git(["for-each-ref", "--format=%(refname:strip=2)", "refs/tags"], git_dir=mirror)
    return sorted(names.decode().split())


def test_the_mirror_keeps_only_the_branchs_tags(checkout: Path, tmp_path: Path) -> None:
    git(checkout, "tag", "-a", "v1", "-m", "First release")
    git(checkout, "tag", "light")
    git(checkout, "checkout", "-q", "-b", "side")
    side = add_commit(checkout, {"side.txt": "b\n"}, "Side work")
    git(checkout, "tag", "-a", "vside", "-m", "Side release")
    git(checkout, "tag", "light-side")
    git(checkout, "checkout", "-q", "develop")
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    assert tags_in(mirror) == ["light", "v1"]
    with pytest.raises(CodetrailError):
        run_git(["cat-file", "-e", side], git_dir=mirror)


def test_the_mirror_follows_deleted_and_moved_tags(checkout: Path, tmp_path: Path) -> None:
    git(checkout, "tag", "-a", "v1", "-m", "First")
    git(checkout, "tag", "old")
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    assert tags_in(mirror) == ["old", "v1"]
    head = add_commit(checkout, {"README.md": "# Changed\n"})
    git(checkout, "tag", "-d", "old")
    git(checkout, "tag", "-f", "-a", "v1", "-m", "Moved")
    git(checkout, "tag", "v2")
    refresh_mirror(mirror, checkout, "develop")
    assert tags_in(mirror) == ["v1", "v2"]
    assert run_git(["rev-parse", "v1^{commit}"], git_dir=mirror).decode().strip() == head


def test_fetching_tags_leaves_the_target_untouched(checkout: Path, tmp_path: Path) -> None:
    git(checkout, "tag", "-a", "v1", "-m", "First")
    git(checkout, "tag", "old")
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    git(checkout, "tag", "-d", "old")
    before = snapshot_tree(checkout)
    refresh_mirror(mirror, checkout, "develop")  # deletes the mirror's stale tag
    assert snapshot_tree(checkout) == before
