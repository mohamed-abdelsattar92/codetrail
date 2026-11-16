"""The "you're behind" signal, from git alone (design section 8.5)."""

from pathlib import Path

import pytest

from codetrail.config import Paths, write_target
from codetrail.lock import target_lock
from codetrail.repo.signal import behind
from codetrail.update import run_update
from tests.fixtures.remote import allow_http_remotes, publish, serving_repository, write_served_target
from tests.fixtures.repos import Commit, add_commit, git, make_repository, snapshot_tree


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")


@pytest.fixture
def checkout(tmp_path: Path, paths: Paths) -> Path:
    repository = make_repository(tmp_path / "target", [{"services/api/main.py": "x = 1\n", "README.md": "r\n"}])
    write_target(paths, "t", repository, "develop")
    return repository


def merge_feature(checkout: Path, name: str, files: Commit) -> None:
    git(checkout, "switch", "-q", "-c", f"feature/{name}")
    add_commit(checkout, files, f"feat: {name}")
    git(checkout, "switch", "-q", "develop")
    git(checkout, "merge", "-q", "--no-ff", "-m", f"Merge {name}", f"feature/{name}")


def test_before_any_update(paths: Paths, checkout: Path) -> None:
    signal = behind(paths, "t")
    assert signal.updated_commit is None
    assert signal.head is not None


def test_up_to_date_after_an_update(paths: Paths, checkout: Path) -> None:
    run_update(paths, "t", facts_only=True)
    signal = behind(paths, "t")
    assert (signal.merges, signal.commits, signal.areas) == (0, 0, [])


def test_counts_merges_and_visible_areas(paths: Paths, checkout: Path) -> None:
    run_update(paths, "t", facts_only=True)
    merge_feature(checkout, "one", {"services/api/orders.py": "y = 2\n"})
    merge_feature(checkout, "two", {".env": "SECRET=1\n", "infra/prod.tfvars": "a = 1\n"})
    before = snapshot_tree(checkout)
    signal = behind(paths, "t")
    assert signal.merges == 2
    assert signal.commits == 4
    assert signal.areas == ["services"]
    assert snapshot_tree(checkout) == before


def test_measures_from_the_commit_the_guide_covered(paths: Paths, checkout: Path) -> None:
    covered = git(checkout, "rev-parse", "HEAD")
    merge_feature(checkout, "one", {"services/api/orders.py": "y = 2\n"})
    run_update(paths, "t", facts_only=True)  # the snapshot moved past the guide's last digest
    merge_feature(checkout, "two", {"infra/main.tf": "a = 1\n"})

    signal = behind(paths, "t", since=covered)

    assert (signal.updated_commit, signal.merges, signal.commits) == (covered, 2, 4)
    assert signal.areas == ["infra", "services"]


@pytest.mark.parametrize("since", ["0" * 40, "--all"])
def test_a_commit_outside_the_history_measures_from_the_snapshot(paths: Paths, checkout: Path, since: str) -> None:
    run_update(paths, "t", facts_only=True)
    merge_feature(checkout, "one", {"services/api/orders.py": "y = 2\n"})

    signal = behind(paths, "t", since=since)

    assert (signal.merges, signal.commits) == (1, 2)


def test_reports_an_update_in_progress_without_fetching(paths: Paths, checkout: Path) -> None:
    run_update(paths, "t", facts_only=True)
    with target_lock(paths, "t"):
        signal = behind(paths, "t")
    assert signal.updating


def test_the_signal_fetches_a_remote_target(paths: Paths, checkout: Path, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    allow_http_remotes(monkeypatch, tmp_path)
    with serving_repository(checkout, tmp_path / "served") as url:
        write_served_target(paths, "remote", url)
        run_update(paths, "remote", facts_only=True)
        merge_feature(checkout, "one", {"services/api/orders.py": "y = 2\n"})
        publish(checkout, tmp_path / "served")
        signal = behind(paths, "remote")
    assert (signal.merges, signal.commits, signal.areas) == (1, 2, ["services"])
    assert signal.head == git(checkout, "rev-parse", "HEAD")
