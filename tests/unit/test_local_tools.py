"""The local provider's file tools: Read, plain-text Grep and Glob, only inside `source/` (design 15.1 and 15.5)."""

import time
from pathlib import Path

import pytest

from codetrail.assistant.local_tools import LocalTools


@pytest.fixture
def tools(tmp_path: Path) -> LocalTools:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("import os\nprint('Hello')\nprint('again')\n")
    (root / "app" / "big.txt").write_text("x" * 5000)
    (root / "README.md").write_text("# Shop\nHello world.\n")
    (tmp_path / "outside.txt").write_text("secret\n")
    return LocalTools(root, max_read_bytes=1000)


def test_read_numbers_lines_and_records_the_file(tools: LocalTools) -> None:
    assert tools.run("read", {"path": "app/main.py"}) == "1\timport os\n2\tprint('Hello')\n3\tprint('again')"
    assert tools.run("read", {"path": "app/main.py", "offset": 2, "limit": 1}) == "2\tprint('Hello')"
    assert tools.files_read == ["app/main.py"]


def test_read_is_cut_at_the_byte_limit(tools: LocalTools) -> None:
    assert len(tools.run("read", {"path": "app/big.txt"})) < 1100


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/hosts", "~/.ssh/id_ed25519", "app/missing.py", "app"])
def test_read_refuses_anything_that_isnt_a_file_inside(tools: LocalTools, path: str) -> None:
    assert tools.run("read", {"path": path}).startswith("Refused:")
    assert tools.files_read == []


def test_grep_searches_plain_text_case_insensitively(tools: LocalTools) -> None:
    assert tools.run("grep", {"text": "hello"}) == "README.md:2: Hello world.\napp/main.py:2: print('Hello')"
    assert tools.run("grep", {"text": "(a+)+$"}) == "No matches."
    assert tools.run("grep", {"text": "print", "glob": "*.md"}) == "No matches."


def test_grep_refuses_outside_folders(tools: LocalTools) -> None:
    assert tools.run("grep", {"text": "secret", "path": ".."}).startswith("Refused:")


def test_glob_lists_matching_files(tools: LocalTools) -> None:
    assert tools.run("glob", {"pattern": "**/*.py"}) == "app/main.py"
    assert tools.run("glob", {"pattern": "../*"}).startswith("Refused:")
    assert tools.run("glob", {"pattern": "/etc/*"}).startswith("Refused:")


def test_a_hostile_glob_pattern_is_refused_or_quick(tools: LocalTools) -> None:
    started = time.monotonic()
    result = tools.run("glob", {"pattern": "*a" * 60 + "b"})
    assert time.monotonic() - started < 2
    assert result.startswith("Refused:") or result == "No matches."


def test_unknown_tools_and_bad_arguments_are_refused(tools: LocalTools) -> None:
    assert tools.run("bash", {"command": "ls"}).startswith("Refused:")
    assert tools.run("read", {"file": "app/main.py"}).startswith("Refused:")
    assert tools.run("read", {"path": 3}).startswith("Refused:")
