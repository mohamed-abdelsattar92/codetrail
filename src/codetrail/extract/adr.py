"""The adr extractor: decisions, their status, and what supersedes what (design section 5.2).

A decision needs a number, from a heading such as `# 0023. Title` or a file name such as `0023-title.md`; files
without one (an index, a template) are skipped. Its status comes from a `Status:` line or the first line under a
`## Status` heading. The body stays text for Claude to read; only this metadata becomes facts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from pathspec import GitIgnoreSpec

from codetrail.extract import FileFacts, Reference, Resolution
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

HEADING = re.compile(r"^#\s+(?:ADR[- ]?)?(\d+)[.:]?\s+(.+?)\s*$", re.IGNORECASE)
FILE_NUMBER = re.compile(r"^(\d+)[-_]")
STATUS_LINE = re.compile(r"^(?:[-*]\s*)?\**status\**\s*:\s*\**\s*(.+)$", re.IGNORECASE)
DATE_LINE = re.compile(r"^(?:[-*]\s*)?\**date\**\s*:\s*\**\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
SUPERSEDED_BY = re.compile(r"superseded\s+by\s+(?:ADR[- ]?)?(\d+)", re.IGNORECASE)


def decision_id(number: str) -> str:
    return f"decision:ADR-{int(number):04d}"


class AdrExtractor:
    name = "adr"
    version = 1

    def __init__(self, globs: Sequence[str]) -> None:
        self._spec = GitIgnoreSpec.from_lines(globs)

    def handles(self, path: str) -> bool:
        return path.endswith(".md") and self._spec.match_file(path)

    def prepare(self, paths: Sequence[str]) -> None:
        return None

    def extract(self, path: str, content: bytes) -> FileFacts:
        lines = content.decode("utf-8", "replace").splitlines()
        number, title = None, None
        for line in lines:
            heading = HEADING.match(line)
            if heading:
                number, title = heading.group(1), heading.group(2)
                break
        if number is None:
            from_name = FILE_NUMBER.match(PurePosixPath(path).name)
            if from_name is None:
                return FileFacts(path)
            number = from_name.group(1)
            title = next(
                (line.lstrip("# ").strip() for line in lines if line.startswith("# ")), PurePosixPath(path).stem
            )
        status_text, date = _status(lines), _date(lines)
        attributes = {"number": f"{int(number):04d}", "title": title or "", "path": path}
        if status_text:
            attributes["status"] = re.split(r"[\s(]", status_text.strip().lower(), maxsplit=1)[0]
        if date:
            attributes["date"] = date
        identity = decision_id(number)
        references = []
        superseded = SUPERSEDED_BY.search(status_text or "")
        if superseded:
            references.append(Reference(decision_id(superseded.group(1)), RelationKind.SUPERSEDES, identity,
                                        (Source(path),)))  # fmt: skip
        return FileFacts(path, (Entity(identity, EntityKind.DECISION, attributes, (Source(path),)),), tuple(references))

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        relations = [
            Relation(reference.source_id, reference.kind, reference.target, {}, reference.sources)
            for file in files
            for reference in file.references
        ]
        return Resolution(relations)


def _status(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        match = STATUS_LINE.match(line.strip())
        if match:
            return match.group(1)
        if re.match(r"^#{2,}\s*status\s*$", line.strip(), re.IGNORECASE):
            return next((following.strip() for following in lines[index + 1 :] if following.strip()), None)
    return None


def _date(lines: list[str]) -> str | None:
    for line in lines:
        match = DATE_LINE.match(line.strip())
        if match:
            return match.group(1)
    return None
