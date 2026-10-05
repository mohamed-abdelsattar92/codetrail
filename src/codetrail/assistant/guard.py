"""The tool guard: the assistant may only read `source/`, with Read, Grep and Glob (design section 6.9).

It runs as a PreToolUse hook, which sees every tool call, read-only ones included, before it runs. Anything it can't
place inside `source/` is refused; every file Read opens is recorded, and becomes the page's `files`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

ALLOWED_TOOLS = ("Read", "Grep", "Glob")
# The channel structured answers come back through; it reads nothing and takes no path.
ANSWER_TOOL = "StructuredOutput"


class ToolGuard:
    def __init__(self, source_root: Path) -> None:
        self.root = source_root.resolve()
        self.files_read: list[str] = []

    def decide(self, tool: str, arguments: Mapping[str, Any]) -> str | None:
        """None to allow the call, or the reason it is refused; any error refuses (fails closed)."""
        try:
            return self._decide(tool, arguments)
        except Exception:
            return "The request couldn't be checked, so it is refused."

    def _decide(self, tool: str, arguments: Mapping[str, Any]) -> str | None:
        if tool == ANSWER_TOOL:
            return None
        if tool not in ALLOWED_TOOLS:
            return f"{tool} isn't available; only Read, Grep and Glob inside the repository are."
        if tool == "Read":
            path = arguments.get("file_path")
            if not isinstance(path, str):
                return "Read needs a file path."
            inside = self._inside(path)
            if inside is None or not inside.is_file():
                return f"{path} isn't a file in the repository."
            relative = inside.relative_to(self.root).as_posix()
            if relative not in self.files_read:
                self.files_read.append(relative)
            return None
        folder = arguments.get("path")
        if folder is not None and (not isinstance(folder, str) or self._inside(folder) is None):
            return f"{folder} isn't inside the repository."
        for key in ("pattern", "glob") if tool == "Glob" else ("glob",):
            pattern = arguments.get(key)
            # Braces could expand to .. or an absolute path before the pattern is resolved; refuse them outright.
            if isinstance(pattern, str) and (
                pattern.startswith(("/", "~")) or ".." in pattern or any(char in pattern for char in "{}\\\0")
            ):
                return f"The pattern {pattern} could reach outside the repository."
        return None

    def _inside(self, path: str) -> Path | None:
        if "\0" in path or path.startswith("~"):
            return None
        candidate = Path(path)
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        return resolved if resolved == self.root or resolved.is_relative_to(self.root) else None

    async def hook(self, input_data: Mapping[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
        arguments = input_data.get("tool_input")
        if not isinstance(arguments, Mapping):
            arguments = {"invalid": True}
        reason = self.decide(str(input_data.get("tool_name", "")), arguments)
        if reason is None and arguments.get("invalid") is True and len(arguments) == 1:
            reason = "The request couldn't be checked, so it is refused."
        if reason is None:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
