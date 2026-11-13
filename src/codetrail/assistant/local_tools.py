"""The local provider's file tools, implemented by Codetrail: Read, Grep and Glob, only inside `source/` (design 15.1).

Every path goes through the tool guard. Grep searches for plain text, never a regular expression, so no pattern from
the model can run away; Glob patterns are short, with few wildcards, and are matched one path segment at a time with
fnmatch, for the same reason. A search runs in a worker thread, which nothing can stop, so it checks the call's deadline
at every file and stops itself once it passes. A refusal comes back to the model as text starting with "Refused:", so it
can try something else.
"""

from __future__ import annotations

import fnmatch
import math
import time
from collections.abc import Mapping, Sequence
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

    def run(self, name: str, arguments: Mapping[str, Any], deadline: float = math.inf) -> str:
        """Runs one tool call; a search stops at `deadline`, a `time.monotonic()` value."""
        try:
            if name == "read":
                return self._read(
                    _text(arguments, "path"), _number(arguments, "offset", 1), _number(arguments, "limit", 2000)
                )
            if name == "grep":
                folder = _text(arguments, "path", ".")
                return self._grep(_text(arguments, "text"), folder, arguments.get("glob"), deadline)
            if name == "glob":
                return self._glob(_text(arguments, "pattern"), _text(arguments, "path", "."), deadline)
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

    def _grep(self, text: str, path: str, glob: object, deadline: float) -> str:
        if not text:
            raise _Refused("grep needs some text to find.")
        folder = self._folder(path)
        pattern = _pattern(glob) if glob is not None else None
        needle = text.lower()
        found: list[str] = []
        for file in self._files(folder, deadline):
            _check(deadline)
            if pattern and not fnmatch.fnmatch(file.name, pattern):
                continue
            relative = file.relative_to(self.root).as_posix()
            with file.open("rb") as handle:
                content = handle.read(self.max_read_bytes).decode("utf-8", "replace")
            for number, line in enumerate(content.splitlines(), start=1):
                if needle in line.lower():
                    found.append(f"{relative}:{number}: {line.strip()[:MAX_LINE_CHARS]}")
                    if len(found) >= MAX_GREP_LINES:
                        return "\n".join(found)
        return "\n".join(found) or "No matches."

    def _glob(self, pattern: str, path: str, deadline: float) -> str:
        reason = self.guard.decide("Glob", {"pattern": _pattern(pattern), "path": path})
        if reason is not None:
            raise _Refused(reason)
        folder = self._folder(path)
        segments = [segment for segment in pattern.split("/") if segment not in ("", ".")]
        paths = [
            file.relative_to(self.root).as_posix()
            for file in self._files(folder, deadline)
            if _matches(file.relative_to(folder).parts, segments)
        ]
        return "\n".join(paths[:MAX_GLOB_PATHS]) or "No matches."

    def _files(self, folder: Path, deadline: float) -> list[Path]:
        """The plain files under a folder, in order; the walk stops at the deadline."""
        found = []
        for file in folder.rglob("*"):  # symlinked folders aren't followed
            _check(deadline)
            if self._plain_file(file):
                found.append(file)
        return sorted(found)

    def _plain_file(self, file: Path) -> bool:
        """A regular file inside the root, reached through no symlink: source/ never holds one, but don't rely on it."""
        if file.is_symlink() or not file.is_file():
            return False
        resolved = file.resolve()
        return resolved.is_relative_to(self.root) and resolved == file.absolute()

    def _folder(self, path: str) -> Path:
        reason = self.guard.decide("Grep", {"path": path})
        folder = (self.root / path).resolve()
        if reason is not None or not folder.is_dir():
            raise _Refused(reason or f"{path} isn't a folder in the repository.")
        return folder


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _Refused("the call's time limit passed, so the search stopped.")


def _matches(parts: Sequence[str], segments: Sequence[str]) -> bool:
    """Whether a path's parts match a glob's segments, where `**` stands for any number of parts. It follows the set
    of segments reached so far, so it takes at most parts x segments steps whatever the pattern, and fnmatch matches
    each part without backtracking across a long name."""
    reached = _past_stars({0}, segments)
    for part in parts:
        following = set()
        for index in reached:
            if index < len(segments) and segments[index] == "**":
                following.add(index)
            elif index < len(segments) and fnmatch.fnmatchcase(part, segments[index]):
                following.add(index + 1)
        reached = _past_stars(following, segments)
        if not reached:
            return False
    return len(segments) in reached


def _past_stars(reached: set[int], segments: Sequence[str]) -> set[int]:
    """The segments reached, and those after any `**` among them, which can match no parts at all."""
    found = set(reached)
    for index in reached:
        while index < len(segments) and segments[index] == "**":
            index += 1
            found.add(index)
    return found


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
