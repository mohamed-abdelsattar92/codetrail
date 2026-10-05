"""gitleaks scanning, with Codetrail's own configuration so a target can't switch it off (design section 3.4)."""

import shutil
from pathlib import Path

import pytest

from codetrail.errors import CodetrailError
from codetrail.repo.secrets import SecretScanner
from tests.fixtures.repos import fake_github_token

GITLEAKS = "gitleaks"


@pytest.fixture
def scanner() -> SecretScanner:
    return SecretScanner(GITLEAKS)


def test_finds_a_token_in_a_file(scanner: SecretScanner, tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "settings.py").write_text(f'TOKEN = "{fake_github_token()}"\n')
    (tmp_path / "README.md").write_text("Nothing secret here.\n")
    findings = scanner.scan_directory(tmp_path)
    assert [(finding.path, finding.rule, finding.line) for finding in findings] == [
        ("app/settings.py", "github-pat", 1)
    ]


def test_a_target_cannot_switch_the_scan_off(scanner: SecretScanner, tmp_path: Path) -> None:
    token = fake_github_token()
    (tmp_path / "settings.py").write_text(f'TOKEN = "{token}"  # gitleaks:allow\n')
    (tmp_path / ".gitleaks.toml").write_text('[allowlist]\npaths = [".*"]\n')
    assert [finding.path for finding in scanner.scan_directory(tmp_path)] == ["settings.py"]


def test_a_tree_carrying_an_ignore_file_is_refused(scanner: SecretScanner, tmp_path: Path) -> None:
    # gitleaks always loads <scanned folder>/.gitleaksignore; source/ never holds one, and the scanner fails closed.
    (tmp_path / "settings.py").write_text(f'TOKEN = "{fake_github_token()}"\n')
    fingerprint = f"{(tmp_path / 'settings.py').resolve()}:github-pat:1"
    (tmp_path / ".gitleaksignore").write_text(fingerprint + "\n")
    with pytest.raises(CodetrailError, match=r"\.gitleaksignore"):
        scanner.scan_directory(tmp_path)


def test_an_ignore_file_in_the_working_directory_has_no_effect(
    scanner: SecretScanner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".gitleaksignore").write_text("".join(f":github-pat:{line}\n" for line in range(1, 10)))
    monkeypatch.chdir(tmp_path)
    assert len(scanner.scan_text(f"x\nTOKEN = '{fake_github_token()}'\n")) == 1


def test_an_environment_setting_cannot_switch_the_scan_off(
    scanner: SecretScanner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allow_all = tmp_path / "allow.toml"
    allow_all.write_text('[allowlist]\npaths = [".*"]\n')
    monkeypatch.setenv("GITLEAKS_CONFIG", str(allow_all))
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML", '[allowlist]\npaths = [".*"]\n')
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "settings.py").write_text(f'TOKEN = "{fake_github_token()}"\n')
    assert len(scanner.scan_directory(tree)) == 1


def test_a_clean_tree_has_no_findings(scanner: SecretScanner, tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n")
    assert scanner.scan_directory(tmp_path) == []


def test_scans_text(scanner: SecretScanner) -> None:
    diff = f"diff --git a/x b/x\n-TOKEN = '{fake_github_token()}'\n+TOKEN = load()\n"
    findings = scanner.scan_text(diff)
    assert [(finding.rule, finding.line) for finding in findings] == [("github-pat", 2)]


def test_findings_never_carry_the_value(scanner: SecretScanner, tmp_path: Path) -> None:
    token = fake_github_token()
    (tmp_path / "settings.py").write_text(f'TOKEN = "{token}"\n')
    assert token not in repr(scanner.scan_directory(tmp_path))
    assert token not in repr(scanner.scan_text(token))


def test_a_missing_scanner_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(CodetrailError, match="gitleaks"):
        SecretScanner(str(tmp_path / "no-such-gitleaks")).scan_directory(tmp_path)


def write_script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def test_a_failing_scanner_says_why_and_how_to_point_at_another(tmp_path: Path) -> None:
    broken = write_script(
        tmp_path / "bin" / "gitleaks", r"printf '\033[31mmise ERROR No version is set\033[0m\n' >&2; exit 1"
    )
    with pytest.raises(CodetrailError) as raised:
        SecretScanner(str(broken)).scan_text("x")
    message = str(raised.value)
    assert "mise ERROR No version is set" in message
    assert "[tools] gitleaks" in message
    assert "\033" not in message


@pytest.fixture
def mise_shim(tmp_path: Path) -> Path:
    """A stand-in for a mise shim: it knows gitleaks' version only in a folder whose mise.toml sets one."""
    real_gitleaks = shutil.which(GITLEAKS)
    assert real_gitleaks is not None
    mise = write_script(
        tmp_path / "mise" / "bin" / "mise",
        f'if [ "$1" = which ] && [ -f mise.toml ]; then echo "{real_gitleaks}"; exit 0; fi\n'
        'if [ "$1" = which ]; then echo "mise ERROR gitleaks is not currently active" >&2; exit 1; fi\n'
        'echo "mise ERROR No version is set for shim: gitleaks" >&2; exit 1\n',
    )
    shim = tmp_path / "mise" / "shims" / "gitleaks"
    shim.parent.mkdir()
    shim.symlink_to(mise)
    return shim


def test_a_mise_shim_runs_the_version_set_where_codetrail_was_started(
    mise_shim: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "mise.toml").write_text('[tools]\ngitleaks = "8"\n')
    monkeypatch.chdir(project)
    assert len(SecretScanner(str(mise_shim)).scan_text(f"TOKEN = '{fake_github_token()}'\n")) == 1


def test_a_mise_shim_without_a_version_says_why(
    mise_shim: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(CodetrailError, match=r"gitleaks is not currently active.*\[tools\] gitleaks"):
        SecretScanner(str(mise_shim)).scan_text("x")
