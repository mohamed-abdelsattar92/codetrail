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


def test_symlinks_are_never_followed(tools: LocalTools, tmp_path: Path) -> None:
    """source/ never holds symlinks, but the tools don't rely on that: one pointing outside is ignored."""
    (tools.root / "app" / "link.txt").symlink_to(tmp_path / "outside.txt")
    (tools.root / "linked").symlink_to(tmp_path, target_is_directory=True)
    assert "secret" not in tools.run("grep", {"text": "secret"})
    assert "link" not in tools.run("glob", {"pattern": "**/*.txt"})
    assert tools.run("read", {"path": "app/link.txt"}).startswith("Refused:")


@pytest.mark.parametrize(("name", "arguments"), [("grep", {"text": "hello"}), ("glob", {"pattern": "**/*.py"})])
def test_a_search_stops_at_the_calls_deadline(tools: LocalTools, name: str, arguments: dict[str, str]) -> None:
    """A search left running in its thread after the call's time limit stops at its next file (design 15.5)."""
    assert tools.run(name, arguments, deadline=time.monotonic() - 1).startswith("Refused: the call's time limit")


def test_glob_matches_like_a_glob(tools: LocalTools) -> None:
    (tools.root / "app" / "deep" / "er").mkdir(parents=True)
    (tools.root / "app" / "deep" / "er" / "x.py").write_text("")
    (tools.root / "top.py").write_text("")
    assert tools.run("glob", {"pattern": "**/*.py"}) == "app/deep/er/x.py\napp/main.py\ntop.py"
    assert tools.run("glob", {"pattern": "*.py"}) == "top.py"
    assert tools.run("glob", {"pattern": "app/**/x.py"}) == "app/deep/er/x.py"
    assert tools.run("glob", {"pattern": "**/deep/**"}) == "app/deep/er/x.py"
    assert tools.run("glob", {"pattern": "*.py", "path": "app"}) == "app/main.py"
    assert tools.run("glob", {"pattern": "**/er/*.p?"}) == "app/deep/er/x.py"


def test_a_glob_that_would_backtrack_is_quick(tools: LocalTools) -> None:
    """pathlib's glob tries every split of a long name between many wildcards; fnmatch's matching doesn't."""
    (tools.root / ("a" * 250)).write_text("")
    started = time.monotonic()
    assert tools.run("glob", {"pattern": "*a*a*a*a*a*a*a*a*a*b"}) == "No matches."
    assert tools.run("glob", {"pattern": "**/" * 5 + "b"}) == "No matches."
    assert time.monotonic() - started < 2
