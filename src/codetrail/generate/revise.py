"""Revising a page: the sections the assistant rewrote, put into the page in place of the old ones (design 6.3).

A section is a heading and everything under it up to the next heading of the same or a higher level, so replacing
`## How it works` replaces its `###` subsections too. Lines inside code fences are never headings. A section whose
heading the page doesn't have is added at the end; one with an empty body is removed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

HEADING = re.compile(r"(#{1,6})\s+\S")
FENCE = re.compile(r"(```|~~~)")


def apply_sections(body: str, sections: Sequence[Mapping[str, object]]) -> str:
    lines = body.rstrip("\n").split("\n")
    for section in sections:
        heading = str(section.get("heading", "")).strip()
        if not HEADING.match(heading):
            continue
        text = _without_heading(str(section.get("body", "")), heading)
        new = [heading, "", *text.split("\n")] if text else []
        start, end = _section(lines, heading)
        if start is None:
            if new:
                lines += ["", *new]
            continue
        if new and end < len(lines) and lines[end].strip():  # keep a blank line before the next heading
            new.append("")
        lines[start:end] = new
        if not new and 0 < start < len(lines) and not lines[start - 1].strip() and not lines[start].strip():
            del lines[start]  # the blank lines on both sides of a removed section become one
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines) + "\n"


def _without_heading(text: str, heading: str) -> str:
    """The section's text, without the heading line a model may repeat at its top."""
    text = text.strip("\n")
    first, _, rest = text.partition("\n")
    return rest.strip("\n") if first.strip() == heading else text


def _section(lines: list[str], heading: str) -> tuple[int | None, int]:
    """Where the section under the heading starts and ends (the line after it), outside code fences."""
    start: int | None = None
    level = 0
    fenced = False
    for index, line in enumerate(lines):
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        match = None if fenced else HEADING.match(line)
        if match is None:
            continue
        if start is not None and len(match.group(1)) <= level:
            return start, _trim_blank(lines, start, index)
        if start is None and line.strip() == heading:
            start, level = index, len(match.group(1))
    return start, len(lines)


def _trim_blank(lines: list[str], start: int, end: int) -> int:
    """The section's end, before the blank lines that separate it from the next heading."""
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end
