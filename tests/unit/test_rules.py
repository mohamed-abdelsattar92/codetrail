"""The exclusion rules: built-in secret patterns and gitignore-style ignore lines (design section 3.3)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from codetrail.config import Paths, SourceSettings
from codetrail.errors import CodetrailError
from codetrail.repo.mirror import refresh_mirror
from codetrail.repo.refresh import ignore_lines, target_rules
from codetrail.repo.rules import ExclusionRules, Reason
from tests.fixtures.repos import make_repository

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


class TickingClock:
    """A clock that moves one second each time it is read, so a test spends time without waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def test_matching_the_target_lines_past_the_time_budget_fails() -> None:
    rules = ExclusionRules(["*.log"], match_budget_seconds=2.5, clock=TickingClock())
    assert rules.reason("a.log") is Reason.IGNORED  # one second spent
    assert rules.reason("b.txt") is None  # two
    with pytest.raises(CodetrailError, match=r"\.codetrailignore is too slow.*2\.5 seconds.*max_ignore_match_seconds"):
        rules.reason("c.txt")


def test_paths_the_built_in_patterns_decide_spend_nothing() -> None:
    rules = ExclusionRules(["*.log"], match_budget_seconds=1.5, clock=TickingClock())
    for _ in range(5):
        assert rules.reason(".env") is Reason.SECRET_PATTERN
        assert rules.reason("x/.gitleaksignore") is Reason.SCANNER_SETTINGS
    assert rules.reason("a.log") is Reason.IGNORED


def test_without_a_budget_matching_is_never_stopped() -> None:
    rules = ExclusionRules(["*.log"], clock=TickingClock())
    for index in range(10):
        assert rules.reason(f"{index}.txt") is None


def test_a_crafted_line_and_path_trip_a_small_budget_on_the_real_clock() -> None:
    """Within the wildcard limit, `*a*a*b` costs about 30 ms on a path of fifteen 255-byte names of `a`s."""
    rules = ExclusionRules(["*a*a*b"], match_budget_seconds=0.001)
    with pytest.raises(CodetrailError, match="too slow"):
        rules.reason("/".join(["a" * 255] * 15))


def test_the_budget_applies_only_when_the_target_has_its_own_lines(tmp_path: Path) -> None:
    target = make_repository(tmp_path / "target", [{".codetrailignore": "*a*a*b\n", "a.md": "a\n"}])
    plain = make_repository(tmp_path / "plain", [{"a.md": "a\n"}])
    limits = SourceSettings(max_ignore_match_seconds=0.001)
    paths = make_paths(tmp_path)
    paths.ignore_file("app").parent.mkdir(parents=True)
    paths.ignore_file("app").write_text("*a*a*b\n")  # the reader's own lines aren't timed
    deep = "/".join(["a" * 255] * 15)
    for repository, slow in ((target, True), (plain, False)):
        mirror = tmp_path / f"{repository.name}.git"
        rules = target_rules(paths, "app", mirror, refresh_mirror(mirror, repository, "develop"), limits)
        if slow:
            with pytest.raises(CodetrailError, match="max_ignore_match_seconds"):
                rules.reason(deep)
        else:
            assert rules.reason(deep) is None


def test_the_match_budget_must_be_positive_and_finite() -> None:
    for value in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            SourceSettings(max_ignore_match_seconds=value)


def test_a_target_line_longer_than_any_path_fails_the_refresh(tmp_path: Path) -> None:
    """No allowed path is longer than 4,096 bytes, so such a line can only cost time."""
    with pytest.raises(CodetrailError, match=r"line 2 is 4097 bytes.*4096"):
        ignore_lines(make_paths(tmp_path), "app", b"build/\n" + b"a" * 4097 + b"\n", MAX_LINES)
    assert ignore_lines(make_paths(tmp_path), "app", b"a" * 4096 + b"\n", MAX_LINES) == ["a" * 4096]


def test_the_ignore_file_has_its_own_size_cap(tmp_path: Path) -> None:
    target = make_repository(tmp_path / "target", [{".codetrailignore": "#" * 99 + "\n", "a.md": "a\n"}])
    mirror = tmp_path / "mirror.git"
    commit = refresh_mirror(mirror, target, "develop")
    with pytest.raises(CodetrailError, match=r"100 bytes.*at most 99 \(\[source\] max_ignore_bytes\)"):
        target_rules(make_paths(tmp_path), "app", mirror, commit, SourceSettings(max_ignore_bytes=99))
    assert SourceSettings().max_ignore_bytes == 64 * 1024
