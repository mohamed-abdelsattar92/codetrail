"""The mirror: a bare clone of the target, fetched over file:// so the target is only read (design section 3.2)."""

import subprocess
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


SETTING = "[source] max_file_bytes"


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
    assert entries["README.md"].size == len(b"# Hello\n")
    assert read_file_at(mirror, commit, "README.md", max_bytes=100, setting=SETTING) == b"# Hello\n"
    assert read_file_at(mirror, commit, "missing.txt", max_bytes=100, setting=SETTING) is None


def test_a_file_over_the_cap_is_never_read(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.git"
    commit = refresh_mirror(mirror, checkout, "develop")
    with pytest.raises(CodetrailError, match=r"README.md is 8 bytes.*at most 7 \(\[source\] max_file_bytes\)"):
        read_file_at(mirror, commit, "README.md", max_bytes=7, setting=SETTING)


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


def test_a_failed_tag_cleanup_never_names_the_tag(checkout: Path, tmp_path: Path) -> None:
    git(checkout, "tag", "unscanned-tag-name")
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    git(checkout, "tag", "-d", "unscanned-tag-name")
    (mirror / "refs" / "tags" / "unscanned-tag-name.lock").write_text("")  # a lock left behind makes the delete fail
    with pytest.raises(CodetrailError) as raised:
        refresh_mirror(mirror, checkout, "develop")
    assert "unscanned-tag-name" not in str(raised.value)
    assert "stale tag" in str(raised.value)


def test_a_failed_tag_fetch_never_names_the_tag(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    git(checkout, "tag", "another-unscanned-name")
    (mirror / "refs" / "tags" / "another-unscanned-name.lock").write_text("")  # left by a git killed mid-fetch
    with pytest.raises(CodetrailError) as raised:
        refresh_mirror(mirror, checkout, "develop")
    assert "another-unscanned-name" not in str(raised.value)
    assert "tags" in str(raised.value)


def write_object(repository: Path, kind: str, data: bytes) -> str:
    """Writes an object as given, malformed or not, and returns its id."""
    written = subprocess.run(
        ["git", "-C", str(repository), "hash-object", "-t", kind, "-w", "--literally", "--stdin"],
        input=data, capture_output=True, check=True,
    )  # fmt: skip
    return written.stdout.decode().strip()


def commit_tree(repository: Path, entries: list[tuple[bytes, bytes, str]], parent: str | None = None) -> str:
    """Points develop at a new commit of a tree written byte for byte from (mode, name, object id) entries."""
    tree = write_object(repository, "tree", b"".join(mode + b" " + name + b"\0" + bytes.fromhex(oid)
                                                    for mode, name, oid in entries))  # fmt: skip
    header = f"tree {tree}\n" + (f"parent {parent}\n" if parent else "")
    commit = write_object(repository, "commit", f"{header}author A <a@x> 1700000000 +0000\n"
                                                f"committer A <a@x> 1700000000 +0000\n\nx\n".encode())  # fmt: skip
    git(repository, "update-ref", "refs/heads/develop", commit)
    return commit


def test_an_object_git_considers_dangerous_is_refused(checkout: Path, tmp_path: Path) -> None:
    """git checks every object it receives (transfer.fsckObjects): a tree entry named .git never lands."""
    blob = write_object(checkout, "blob", b"x\n")
    commit_tree(checkout, [(b"100644", b".git", blob)])
    with pytest.raises(CodetrailError, match="hasDotgit"):
        refresh_mirror(tmp_path / "mirror.git", checkout, "develop")


def test_a_fetch_checks_objects_too(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.git"
    refresh_mirror(mirror, checkout, "develop")
    blob = write_object(checkout, "blob", b"x\n")
    commit_tree(checkout, [(b"100644", b".git", blob)], parent=git(checkout, "rev-parse", "HEAD"))
    with pytest.raises(CodetrailError, match="hasDotgit"):
        refresh_mirror(mirror, checkout, "develop")


def test_a_malformed_gitmodules_is_refused(tmp_path: Path) -> None:
    target = make_repository(tmp_path / "target", [{".gitmodules": '[submodule "x"]\n\tpath = x\n\turl = -u./evil\n'}])
    with pytest.raises(CodetrailError, match="gitmodulesUrl"):
        refresh_mirror(tmp_path / "mirror.git", target, "develop")


def test_cosmetic_flaws_of_old_repositories_still_mirror(checkout: Path, tmp_path: Path) -> None:
    """A zero-padded file mode and an author with no email or a bad date are common in old histories, and harmless."""
    blob = write_object(checkout, "blob", b"old\n")
    tree = write_object(checkout, "tree", b"0100644 old.txt\0" + bytes.fromhex(blob))
    commit = write_object(checkout, "commit", f"tree {tree}\nauthor Someone 99999999999999999999 +0000\n"
                                              f"committer Someone <s@x>1700000000 +99\n\nold\n".encode())  # fmt: skip
    git(checkout, "update-ref", "refs/heads/develop", commit)
    assert refresh_mirror(tmp_path / "mirror.git", checkout, "develop") == commit
