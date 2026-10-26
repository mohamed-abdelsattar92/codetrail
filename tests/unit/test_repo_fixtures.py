"""The repositories tests build stay still while a test looks at them (design section 11)."""

from pathlib import Path

from tests.fixtures.repos import git, make_repository


def test_fixture_repositories_run_no_background_maintenance(tmp_path: Path) -> None:
    # A merge or commit can start git's maintenance in the background, which writes and removes files under .git
    # while a test compares the folder before and after Codetrail read it (seen in CI on a release).
    repository = make_repository(tmp_path / "repository", [{"README.md": "# R\n"}])
    assert git(repository, "config", "--get", "maintenance.auto") == "false"
    assert git(repository, "config", "--get", "gc.auto") == "0"
