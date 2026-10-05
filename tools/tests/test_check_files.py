"""Tests for the staged-file check, tools/hooks/check_files.py."""
import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "check_files", pathlib.Path(__file__).resolve().parents[1] / "hooks" / "check_files.py")
check_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_files)

MB = 1024 * 1024


@pytest.mark.parametrize("path,size", [
    ("src/codetrail/cli.py", 2 * MB),
    ("docs/design/huge.md", MB + 1),
    ("tests/fixtures/big.json", 3 * MB),
])
def test_rejects_large_files_outside_the_vendor_folder(path: str, size: int) -> None:
    assert check_files.problems(path, size)


def test_allows_large_vendored_files() -> None:
    # Mermaid, vendored for the page (ADR 0004), is about 3 MB.
    assert check_files.problems("src/codetrail/web/static/vendor/mermaid.min.js", 3 * MB) == []


def test_allows_small_files_anywhere() -> None:
    assert check_files.problems("src/codetrail/cli.py", MB) == []
