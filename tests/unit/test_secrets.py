"""gitleaks scanning, with Codetrail's own configuration so a target can't switch it off (design section 3.4)."""

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
    (tmp_path / ".gitleaksignore").write_text("settings.py:github-pat:1\n")
    assert [finding.path for finding in scanner.scan_directory(tmp_path)] == ["settings.py"]


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
