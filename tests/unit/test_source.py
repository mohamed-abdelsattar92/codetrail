"""Materializing a target's allowed files into source/ (design sections 3.2 to 3.4)."""

import shutil
from pathlib import Path

import pytest

from codetrail.errors import CodetrailError
from codetrail.repo.mirror import TreeEntry, refresh_mirror
from codetrail.repo.rules import ExclusionRules, Reason
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest, build_source, safe_path
from tests.fixtures.repos import Symlink, commit_entries, fake_github_token, make_repository


@pytest.fixture
def hostile(tmp_path: Path) -> Path:
    return make_repository(
        tmp_path / "target",
        [
            {
                "README.md": "# Target\n",
                "app/main.py": "print('hi')\n",
                "app/settings.py": f'TOKEN = "{fake_github_token()}"\n',
                ".env": "PASSWORD=not-a-real-one\n",
                ".env.example": "PASSWORD=\n",
                "infra/prod.tfvars": 'region = "x"\n',
                "keys/server.pem": "not a real key\n",
                "docs/private/notes.md": "private\n",
                "docs/design/logo.png": b"\x89PNG fake",
                "escape": Symlink("/etc/passwd"),
            }
        ],
    )


def build(hostile: Path, tmp_path: Path, scanner: SecretScanner | None = None) -> SourceManifest:
    mirror = tmp_path / "data" / "mirror.git"
    commit = refresh_mirror(mirror, hostile, "develop")
    rules = ExclusionRules(["docs/private/", "**/*.png"])
    return build_source(mirror, commit, rules, scanner or SecretScanner("gitleaks"), tmp_path / "data")


def test_only_allowed_files_are_materialized(hostile: Path, tmp_path: Path) -> None:
    manifest = build(hostile, tmp_path)
    source = tmp_path / "data" / "source"
    on_disk = sorted(str(path.relative_to(source)) for path in source.rglob("*") if path.is_file() or path.is_symlink())
    assert on_disk == [".env.example", "README.md", "app/main.py"]
    assert sorted(manifest.files) == on_disk
    assert (source / "app" / "main.py").read_text() == "print('hi')\n"


def test_each_exclusion_has_its_reason(hostile: Path, tmp_path: Path) -> None:
    manifest = build(hostile, tmp_path)
    reasons = {item.path: (item.reason, item.rule) for item in manifest.excluded}
    assert reasons == {
        ".env": (Reason.SECRET_PATTERN, None),
        "infra/prod.tfvars": (Reason.SECRET_PATTERN, None),
        "keys/server.pem": (Reason.SECRET_PATTERN, None),
        "docs/private/notes.md": (Reason.IGNORED, None),
        "docs/design/logo.png": (Reason.IGNORED, None),
        "app/settings.py": (Reason.GITLEAKS, "github-pat"),
        "escape": (Reason.NOT_A_FILE, None),
    }


def test_the_manifest_is_saved_and_loads_back(hostile: Path, tmp_path: Path) -> None:
    manifest = build(hostile, tmp_path)
    assert SourceManifest.load(tmp_path / "data" / "source.json") == manifest
    assert SourceManifest.load(tmp_path / "nothing.json") is None


def test_a_rebuild_replaces_the_previous_source(hostile: Path, tmp_path: Path) -> None:
    build(hostile, tmp_path)
    stray = tmp_path / "data" / "source" / "stray.txt"
    stray.write_text("left over")
    build(hostile, tmp_path)
    assert not stray.exists()
    assert not (tmp_path / "data" / "source.next").exists()


def test_a_failed_scan_leaves_the_previous_source_intact(hostile: Path, tmp_path: Path) -> None:
    build(hostile, tmp_path)
    before = sorted(path.name for path in (tmp_path / "data" / "source").rglob("*"))
    with pytest.raises(CodetrailError):
        build(hostile, tmp_path, SecretScanner(str(tmp_path / "missing-gitleaks")))
    assert sorted(path.name for path in (tmp_path / "data" / "source").rglob("*")) == before
    assert not (tmp_path / "data" / "source.next").exists()


@pytest.mark.parametrize("path", ["/etc/passwd", "../x", "a/../../x", ".git/config", "a/.GIT/hooks/x", "a//b", ""])
def test_unsafe_paths_are_refused(path: str) -> None:
    assert not safe_path(path)


@pytest.mark.parametrize("path", ["README.md", "a/b/c.py", ".github/workflows/ci.yml", "a/.gitignore"])
def test_ordinary_paths_are_safe(path: str) -> None:
    assert safe_path(path)


def test_tree_entries_are_plain_data() -> None:
    assert TreeEntry(mode="100644", blob="0" * 40, path="x").path == "x"


def test_source_is_inside_the_data_folder_only(hostile: Path, tmp_path: Path) -> None:
    build(hostile, tmp_path)
    shutil.rmtree(tmp_path / "data" / "source")
    build(hostile, tmp_path)
    assert (tmp_path / "data" / "source" / "README.md").exists()


@pytest.mark.parametrize("path", [".g\u200cit/config", "a/.\u200dgit/hooks/x", ".git\ufeff/config"])
def test_spellings_that_fold_to_git_are_refused(path: str) -> None:
    assert not safe_path(path)


def test_names_that_collide_on_disk_are_all_excluded(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "target", [{"ok.md": "fine\n"}])
    commit_entries(
        checkout, {"A.txt": "upper\n", "a.txt": "lower\n", "caf\u00e9.md": "nfc\n", "cafe\u0301.md": "nfd\n"}
    )
    manifest = build(checkout, tmp_path)
    assert list(manifest.files) == ["ok.md"]
    unsafe = sorted(item.path for item in manifest.excluded if item.reason is Reason.UNSAFE_PATH)
    assert unsafe == sorted(["A.txt", "a.txt", "caf\u00e9.md", "cafe\u0301.md"])


def test_folders_that_collide_on_disk_are_excluded_without_failing(tmp_path: Path) -> None:
    checkout = make_repository(tmp_path / "target", [{"ok.md": "fine\n"}])
    commit_entries(checkout, {"Docs/a.md": "a\n", "docs/b.md": "b\n", "Notes": "file\n", "notes/x.md": "x\n"})
    manifest = build(checkout, tmp_path)
    assert list(manifest.files) == ["ok.md"]
    unsafe = sorted(item.path for item in manifest.excluded if item.reason is Reason.UNSAFE_PATH)
    assert unsafe == ["Docs/a.md", "Notes", "docs/b.md", "notes/x.md"]


def test_names_that_are_not_valid_utf8_are_unsafe() -> None:
    assert not safe_path("bad\udcff.md")
