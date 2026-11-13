"""The exclusion rules: built-in secret patterns and gitignore-style ignore lines (design section 3.3)."""

from pathlib import Path

import pytest

from codetrail.config import Paths
from codetrail.errors import CodetrailError
from codetrail.repo.refresh import ignore_lines
from codetrail.repo.rules import ExclusionRules, Reason

SECRETS = [
    ".env",
    ".env.local",
    ".env.production",
    ".dev.vars",
    "AuthKey_ABC.p8",
    "cert.p12",
    "server.pem",
    "release.keystore",
    "upload.jks",
    "terraform.tfstate",
    "terraform.tfstate.backup",
    "prod.tfvars",
    "prod.auto.tfvars.json",
    "service.key",
    "cert.pfx",
    "id_rsa",
    "id_rsa.pub",
    "id_ed25519",
    "putty.ppk",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".terraform/providers/lock",
]


@pytest.mark.parametrize("name", SECRETS)
@pytest.mark.parametrize("folder", ["", "services/api/", "infra/envs/dev/"])
def test_built_in_patterns_exclude_secrets_anywhere(folder: str, name: str) -> None:
    assert ExclusionRules([]).reason(folder + name) is Reason.SECRET_PATTERN


@pytest.mark.parametrize("path", [".env.example", "services/api/.env.example", "terraform.tfvars.example"])
def test_example_files_are_not_secrets(path: str) -> None:
    assert ExclusionRules([]).reason(path) is None


def test_examples_inside_an_excluded_folder_stay_excluded() -> None:
    assert ExclusionRules([]).reason(".terraform/settings.example") is Reason.SECRET_PATTERN


@pytest.mark.parametrize("line", ["!.env", "!**/.env", "!*.pem", "!**/*.pem", "!.terraform/", "!*"])
def test_no_ignore_line_can_bring_back_a_secret(line: str) -> None:
    rules = ExclusionRules([line])
    assert rules.reason(".env") is Reason.SECRET_PATTERN
    assert rules.reason("keys/server.pem") is Reason.SECRET_PATTERN
    assert rules.reason(".terraform/x") is Reason.SECRET_PATTERN


@pytest.mark.parametrize("path", ["README.md", "src/app.py", "docs/environment.md", "envelope.txt", "keys.py"])
def test_ordinary_files_are_visible(path: str) -> None:
    assert ExclusionRules([]).reason(path) is None


@pytest.mark.parametrize(
    ("lines", "path", "excluded"),
    [
        (["docs/"], "docs/a.md", True),
        (["docs/"], "src/docs/b.md", True),
        (["/build"], "build/out.txt", True),
        (["/build"], "src/build/out.txt", False),
        (["**/*.png"], "a/b/c.png", True),
        (["**/*.png"], "a/b/c.jpg", False),
        (["a/**/b"], "a/x/y/b", True),
        (["*.png", "!keep.png"], "keep.png", False),
        (["*.png", "!keep.png"], "other.png", True),
        (["# a comment", "", "   "], "README.md", False),
        (["secret?.txt"], "secret1.txt", True),
        (["data[0-9].csv"], "data7.csv", True),
    ],
)
def test_ignore_lines_follow_gitignore(lines: list[str], path: str, excluded: bool) -> None:
    expected = Reason.IGNORED if excluded else None
    assert ExclusionRules(lines).reason(path) is expected


@pytest.mark.parametrize("path", [".ENV", ".Env.local", "infra/prod.TFVARS", "keys/KEY.PEM", "ID_RSA", ".TERRAFORM/x"])
def test_built_in_patterns_ignore_case(path: str) -> None:
    # macOS volumes are case-insensitive: .ENV is the file a dotenv loader opens as .env.
    assert ExclusionRules([]).reason(path) is Reason.SECRET_PATTERN


@pytest.mark.parametrize("path", [".gitleaksignore", ".gitleaks.toml", "sub/.gitleaksignore", ".GITLEAKSIGNORE"])
def test_scanner_settings_never_reach_the_scan(path: str) -> None:
    assert ExclusionRules(["!.gitleaksignore"]).reason(path) is Reason.SCANNER_SETTINGS


def test_ignore_lines_match_in_any_case() -> None:
    # git on macOS matches .gitignore without regard to case; Codetrail's ignore files do the same.
    assert ExclusionRules(["docs/private/"]).reason("Docs/Private/notes.md") is Reason.IGNORED


@pytest.mark.parametrize(
    "line", ["*?*?*?*?*?*Z", "src/*a*a*a*b", "*a*b*c*d.txt", "*a*/*a*/*a*/*a*/*a*/*b", "**/a/**/a/**/b", "**/*a*/**"]
)
def test_a_target_line_too_slow_to_match_fails_the_refresh(tmp_path: Path, line: str) -> None:
    # pathspec's patterns backtrack: six `*` in one segment take minutes on one long name, and so do six one-`*`
    # segments like `*a*` on a wide path, so a target could hang every update, the page's "behind" signal and a
    # team's catch-up with one committed line.
    with pytest.raises(CodetrailError, match=r"line 2, .*at most 2"):
        ignore_lines(make_paths(tmp_path), "app", f"build/\n{line}\n".encode(), MAX_LINES)


@pytest.mark.parametrize(
    "line", ["*.log", "**/*.min.js", "a*/b*/c*/d*/e*", "*a*b*c", "**/cache/**", "docs/**/*.md", "**/test/**/*.snap"]
)
def test_ordinary_target_lines_pass(tmp_path: Path, line: str) -> None:
    assert ignore_lines(make_paths(tmp_path), "app", f"{line}\n".encode(), MAX_LINES) == [line]


def test_the_readers_own_lines_are_not_limited(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    paths.ignore_file("app").parent.mkdir(parents=True)
    paths.ignore_file("app").write_text("*a*a*a*a*b\n")
    assert ignore_lines(paths, "app", None, MAX_LINES) == ["*a*a*a*a*b"]


MAX_LINES = 200


def test_a_target_file_with_too_many_lines_fails_the_refresh(tmp_path: Path) -> None:
    """Each line costs up to about 2 ms on a crafted 255-byte name (pathspec 1.1.1), so their number is capped."""
    with pytest.raises(CodetrailError, match=r"3 lines with a pattern.*at most 2 \(\[source\] max_ignore_lines\)"):
        ignore_lines(make_paths(tmp_path), "app", b"a\nb\nc\n", 2)


def test_comments_and_blank_lines_are_not_counted(tmp_path: Path) -> None:
    assert ignore_lines(make_paths(tmp_path), "app", b"# build output\n\na\nb\n", 2) == ["# build output", "", "a", "b"]


def test_the_readers_own_lines_are_not_counted(tmp_path: Path) -> None:
    paths = make_paths(tmp_path)
    paths.ignore_file("app").parent.mkdir(parents=True)
    paths.ignore_file("app").write_text("x\ny\nz\n")
    assert ignore_lines(paths, "app", b"a\n", 1) == ["a", "x", "y", "z"]


def make_paths(tmp_path: Path) -> Paths:
    return Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")


@pytest.mark.parametrize("path", ["x/" + "a" * 256, "/".join(["a" * 200] * 21)])
def test_a_path_too_long_for_a_disk_is_unsafe_before_any_pattern_runs(path: str) -> None:
    # The ignore-line limit was measured on names of up to 255 bytes; longer ones only make the matcher slower.
    assert ExclusionRules(["*a*b*c"]).reason(path) == Reason.UNSAFE_PATH


def test_a_name_of_255_bytes_is_matched_as_usual() -> None:
    assert ExclusionRules(["*.md"]).reason("docs/" + "a" * 252 + ".md") == Reason.IGNORED
