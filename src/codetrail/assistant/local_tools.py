"""The local provider's file tools, implemented by Codetrail: Read, Grep and Glob, only inside `source/` (design 15.1).

Every path goes through the tool guard. Grep searches for plain text, never a regular expression, so no pattern from
the model can run away; Glob patterns are short, with few wildcards, for the same reason. A refusal comes back to the
model as text starting with "Refused:", so it can try something else.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from codetrail.assistant.guard import ToolGuard

MAX_GREP_LINES = 200
MAX_GLOB_PATHS = 500
MAX_PATTERN_CHARS = 200
MAX_WILDCARDS = 10
MAX_LINE_CHARS = 300

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a text file in the repository, with line numbers.",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string", "description": "A path relative to the repository's root."},
                    "offset": {"type": "integer", "description": "The first line to read, from 1."},
                    "limit": {"type": "integer", "description": "How many lines to read."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Find lines containing some text (plain text, not case-sensitive).",
            "parameters": {
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "path": {"type": "string", "description": "A folder to search in; the root by default."},
                    "glob": {"type": "string", "description": "Only files whose path matches this pattern, like *.py."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "List the files whose paths match a pattern, like **/*.py.",
            "parameters": {
                "type": "object",
                "required": ["pattern"],
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "A folder to search in; the root by default."},
                },
            },
        },
    },
]


class _Refused(Exception):
    pass


class LocalTools:
    def __init__(self, source_root: Path, max_read_bytes: int) -> None:
        self.guard = ToolGuard(source_root)
        self.root = self.guard.root
        self.max_read_bytes = max_read_bytes

    @property
    def files_read(self) -> list[str]:
        return self.guard.files_read

    def run(self, name: str, arguments: Mapping[str, Any]) -> str:
        try:
            if name == "read":
                return self._read(
                    _text(arguments, "path"), _number(arguments, "offset", 1), _number(arguments, "limit", 2000)
                )
            if name == "grep":
                return self._grep(_text(arguments, "text"), _text(arguments, "path", "."), arguments.get("glob"))
            if name == "glob":
                return self._glob(_text(arguments, "pattern"), _text(arguments, "path", "."))
            raise _Refused(f"{name} isn't a tool here; only read, grep and glob are.")
        except _Refused as refusal:
            return f"Refused: {refusal}"

    def _read(self, path: str, offset: int, limit: int) -> str:
        reason = self.guard.decide("Read", {"file_path": path})
        if reason is not None:
            raise _Refused(reason)
        with (self.root / path).open("rb") as handle:
            data = handle.read(self.max_read_bytes)
        lines = data.decode("utf-8", "replace").splitlines()
        start = max(offset, 1)
        shown = lines[start - 1 : start - 1 + max(limit, 1)]
        return "\n".join(f"{start + index}\t{line}" for index, line in enumerate(shown))

    def _grep(self, text: str, path: str, glob: object) -> str:
        if not text:
            raise _Refused("grep needs some text to find.")
        folder = self._folder(path)
        pattern = _pattern(glob) if glob is not None else None
        needle = text.lower()
        found: list[str] = []
        for file in sorted(folder.rglob("*")):
            relative = file.relative_to(self.root).as_posix()
            if not file.is_file() or (pattern and not fnmatch.fnmatch(file.name, pattern)):
                continue
            with file.open("rb") as handle:
                content = handle.read(self.max_read_bytes).decode("utf-8", "replace")
            for number, line in enumerate(content.splitlines(), start=1):
                if needle in line.lower():
                    found.append(f"{relative}:{number}: {line.strip()[:MAX_LINE_CHARS]}")
                    if len(found) >= MAX_GREP_LINES:
                        return "\n".join(found)
        return "\n".join(found) or "No matches."

    def _glob(self, pattern: str, path: str) -> str:
        reason = self.guard.decide("Glob", {"pattern": _pattern(pattern), "path": path})
        if reason is not None:
            raise _Refused(reason)
        folder = self._folder(path)
        paths = sorted(file.relative_to(self.root).as_posix() for file in folder.glob(pattern) if file.is_file())
        return "\n".join(paths[:MAX_GLOB_PATHS]) or "No matches."

    def _folder(self, path: str) -> Path:
        reason = self.guard.decide("Grep", {"path": path})
        folder = (self.root / path).resolve()
        if reason is not None or not folder.is_dir():
            raise _Refused(reason or f"{path} isn't a folder in the repository.")
        return folder


def _pattern(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _Refused("A pattern must be some text.")
    if len(value) > MAX_PATTERN_CHARS or value.count("*") + value.count("?") > MAX_WILDCARDS:
        raise _Refused(f"Patterns are limited to {MAX_PATTERN_CHARS} characters and {MAX_WILDCARDS} wildcards.")
    return value


def _text(arguments: Mapping[str, Any], key: str, default: str | None = None) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise _Refused(f"{key} must be text.")
    return value


def _number(arguments: Mapping[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _Refused(f"{key} must be a whole number.")
    return value
