"""The tool guard: Claude reads source/ only, with Read, Grep and Glob (design section 6.9)."""

import json
from pathlib import Path

import pytest

from codetrail.assistant.guard import ToolGuard


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("x = 1\n")
    (tmp_path / "outside.txt").write_text("not for Claude\n")
    (root / "link").symlink_to(tmp_path / "outside.txt")  # source/ never holds links; the guard copes anyway
    return root


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Read", {"file_path": "app/main.py"}),
        ("Read", {"file_path": "{root}/app/main.py"}),
        ("Grep", {"pattern": "x"}),
        ("Grep", {"pattern": "x", "path": "app"}),
        ("Glob", {"pattern": "**/*.py"}),
        ("Glob", {"pattern": "*.py", "path": "{root}/app"}),
        ("StructuredOutput", {"answer": "anything"}),
    ],
)
def test_allows_reading_inside_source(source: Path, tool: str, arguments: dict[str, str]) -> None:
    guard = ToolGuard(source)
    resolved = {key: value.replace("{root}", str(source)) for key, value in arguments.items()}
    assert guard.decide(tool, resolved) is None


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("Read", {"file_path": "../outside.txt"}),
        ("Read", {"file_path": "/etc/passwd"}),
        ("Read", {"file_path": "link"}),
        ("Read", {"file_path": "app/missing.py"}),
        ("Read", {}),
        ("Grep", {"pattern": "x", "path": "/"}),
        ("Grep", {"pattern": "x", "path": ".."}),
        ("Grep", {"pattern": "x", "glob": "../**"}),
        ("Glob", {"pattern": "../*"}),
        ("Glob", {"pattern": "/etc/*"}),
        ("Glob", {"pattern": "*", "path": "/Users"}),
        ("Bash", {"command": "cat /etc/passwd"}),
        ("Write", {"file_path": "app/main.py", "content": "x"}),
        ("Edit", {"file_path": "app/main.py"}),
        ("WebFetch", {"url": "https://example.com"}),
        ("Task", {"prompt": "x"}),
        ("mcp__anything__tool", {}),
        ("Glob", {"pattern": "{..,x}/*"}),
        ("Glob", {"pattern": "**/{/etc,x}/*"}),
        ("Grep", {"pattern": "x", "glob": "{..}/*"}),
        ("Glob", {"pattern": "a\\..\\b"}),
        ("Read", {"file_path": "~nosuchuser/x"}),
        ("Read", {"file_path": "a\x00b"}),
    ],
)
def test_refuses_everything_else(source: Path, tool: str, arguments: dict[str, str]) -> None:
    assert ToolGuard(source).decide(tool, arguments) is not None


def test_records_the_files_read(source: Path) -> None:
    guard = ToolGuard(source)
    guard.decide("Read", {"file_path": str(source / "app" / "main.py")})
    guard.decide("Read", {"file_path": "app/main.py"})
    guard.decide("Read", {"file_path": "../outside.txt"})
    assert guard.files_read == ["app/main.py"]


@pytest.mark.anyio
async def test_the_hook_denies_with_a_reason(source: Path) -> None:
    guard = ToolGuard(source)
    denied = await guard.hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, None, {})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    allowed = await guard.hook({"tool_name": "Read", "tool_input": {"file_path": "app/main.py"}}, None, {})
    assert allowed == {}


@pytest.mark.anyio
async def test_the_hook_fails_closed_on_strange_input(source: Path) -> None:
    guard = ToolGuard(source)
    denied = await guard.hook({"tool_name": "Read", "tool_input": ["not", "a", "mapping"]}, None, {})
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("docs/manual.pdf", b"%PDF-1.7\n"),
        ("docs/Manual.PDF", b"plain text"),  # by its name, whatever it holds
        ("docs/logo.png", b"plain text"),
        ("docs/photo.jpeg", b"plain text"),
        ("docs/scan.webp", b"plain text"),
        ("docs/notes.txt", b"\x89PNG\r\n\x1a\n...."),  # by its content, whatever its name
        ("docs/notes.md", b"junk %PDF-1.4\n"),
        ("docs/data.bin", b"\xff\xd8\xff\xe0JFIF"),
        ("docs/anim", b"GIF89a...."),
        ("docs/pic.dat", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
        ("docs/pic.raw", b"II*\x00...."),
        ("docs/pic.heic", b"\x00\x00\x00\x18ftypheic...."),
    ],
)
def test_pdfs_and_images_are_never_read(source: Path, name: str, content: bytes) -> None:
    """Claude Code's Read renders PDFs and images for the model; the secret scan can't read text drawn in them."""
    (source / name).parent.mkdir(parents=True, exist_ok=True)
    (source / name).write_bytes(content)
    guard = ToolGuard(source)
    reason = guard.decide("Read", {"file_path": name})
    assert reason is not None and "PDF or an image" in reason
    assert guard.files_read == []


@pytest.mark.parametrize("name", ["docs/diagram.svg", "docs/BMW.md", "docs/readme.txt"])
def test_text_files_are_still_read(source: Path, name: str) -> None:
    (source / name).parent.mkdir(parents=True, exist_ok=True)
    (source / name).write_text("BM text that starts like a bitmap\n")
    assert ToolGuard(source).decide("Read", {"file_path": name}) is None


def notebook(*outputs: dict[str, object]) -> str:
    return json.dumps({"nbformat": 4, "cells": [{"cell_type": "code", "source": "x", "outputs": list(outputs)}]})


@pytest.mark.parametrize(
    "content",
    [
        notebook({"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo=", "text/plain": "<Figure>"}}),
        notebook({"output_type": "execute_result", "data": {"image/jpeg": "/9j/4AAQ"}}),
        "not json",
        json.dumps({"worksheets": []}),  # an old notebook format, not read
    ],
)
def test_notebooks_with_image_outputs_are_never_read(source: Path, content: str) -> None:
    """Read renders a notebook's image outputs for the model, and the secret scan can't read a base64 image."""
    (source / "analysis.ipynb").write_text(content)
    reason = ToolGuard(source).decide("Read", {"file_path": "analysis.ipynb"})
    assert reason is not None and "image" in reason


def test_notebooks_with_text_outputs_are_read(source: Path) -> None:
    text = notebook({"output_type": "stream", "text": "ok"}, {"data": {"image/svg+xml": "<svg/>", "text/plain": "x"}})
    (source / "analysis.ipynb").write_text(text)
    assert ToolGuard(source).decide("Read", {"file_path": "analysis.ipynb"}) is None


def test_the_name_given_to_read_counts_too(source: Path) -> None:
    """Read picks its renderer from the name it's given, which a link can make differ from the file's own."""
    (source / "notes.txt").write_text("plain text\n")
    (source / "picture.png").symlink_to(source / "notes.txt")
    reason = ToolGuard(source).decide("Read", {"file_path": "picture.png"})
    assert reason is not None and "PDF or an image" in reason


@pytest.mark.parametrize(
    ("attachments", "refused"),
    [
        ({"plot.png": {"image/png": "iVBORw0KGgo="}}, True),
        ({"photo.jpg": {"image/jpeg": "/9j/4AAQ"}}, True),
        ({"diagram.svg": {"image/svg+xml": "<svg/>"}}, False),
        ("not a mapping", True),
    ],
)
def test_notebook_markdown_attachments_with_images_are_refused(
    source: Path, attachments: object, refused: bool
) -> None:
    cell = {"cell_type": "markdown", "source": "![plot](attachment:plot.png)", "attachments": attachments}
    (source / "notes.ipynb").write_text(json.dumps({"nbformat": 4, "cells": [cell]}))
    reason = ToolGuard(source).decide("Read", {"file_path": "notes.ipynb"})
    assert (reason is not None and "image" in reason) is refused
