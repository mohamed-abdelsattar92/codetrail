"""Validation before a page is saved (design section 6.5).

- A documented block cites a visible file with a line range (at most 40 lines) or a commit, and quotes it; the
  quote must appear, whitespace-normalized and contiguous, in those lines or in the commit's message. Commit messages
  are scanned with gitleaks first; a flagged one can't be quoted.
- Fact links must name existing facts; diagram placeholders must name a known diagram over an existing scope.
- Checks must have a question and rubric points grounded in existing facts or visible paths.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from codetrail.errors import CodetrailError
from codetrail.facts import EntityKind
from codetrail.facts.store import FactStore
from codetrail.generate.outline import in_scope
from codetrail.repo.git import run_git
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest

MARKER = re.compile(r"^>\s*\[!(documented|inferred)\]\s*(\S*)\s*$")
FACT_LINK = re.compile(r"\[\[([a-z_]+:[^\]\s]+)\]\]")
DIAGRAM = re.compile(r"^\{\{\s*diagram\b(.*?)\}\}\s*$")
DIAGRAM_KINDS = {"imports": "scope", "dependencies": "project"}
FILE_CITATION = re.compile(r"^(?P<path>[^#\s]+)#L(?P<start>\d+)(?:-L(?P<end>\d+))?$")
COMMIT_CITATION = re.compile(r"^commit:(?P<sha>[0-9a-f]{7,40})$")
MAX_CITED_LINES = 40
QUOTE_MARKS = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb"  # straight and typographic quotation marks


@dataclass(frozen=True)
class RationaleBlock:
    kind: str
    citation: str | None
    text: str
    line: int


@dataclass
class ValidationContext:
    source_root: Path
    manifest: SourceManifest
    store: FactStore
    mirror: Path
    scanner: SecretScanner


def parse_rationale(body: str) -> list[RationaleBlock]:
    blocks = []
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        marker = MARKER.match(lines[index])
        if not marker:
            index += 1
            continue
        start = index
        index += 1
        content = []
        while index < len(lines) and lines[index].startswith(">") and not MARKER.match(lines[index]):
            content.append(lines[index][1:].removeprefix(" "))
            index += 1
        blocks.append(RationaleBlock(marker.group(1), marker.group(2) or None, "\n".join(content).strip(), start + 1))
    return blocks


def normalize(text: str) -> str:
    return " ".join(text.split())


def validate_page(body: str, checks: Sequence[Mapping[str, Any]] | None, context: ValidationContext) -> list[str]:
    problems: list[str] = []
    if not body.strip():
        return ["The page is empty."]
    for block in parse_rationale(body):
        if block.kind == "documented":
            problems += _check_documented(block, context)
    for fact in FACT_LINK.findall(body):
        if context.store.entity(fact) is None:
            problems.append(f"The fact link [[{fact}]] names no existing fact.")
    for line in body.splitlines():
        diagram = DIAGRAM.match(line.strip())
        if diagram:
            problems += _check_diagram(diagram.group(1), context)
    if checks is not None:
        problems += _check_checks(checks, context)
    return problems


def _check_documented(block: RationaleBlock, context: ValidationContext) -> list[str]:
    quote = normalize(block.text).strip(QUOTE_MARKS + " ")
    where = f"The documented block at line {block.line}"
    if not quote:
        return [f"{where} has no quote."]
    citation = block.citation or ""
    file = FILE_CITATION.match(citation)
    commit = COMMIT_CITATION.match(citation)
    if file:
        path, start = file.group("path"), int(file.group("start"))
        end = int(file.group("end") or start)
        if path not in context.manifest.files:
            return [f"{where} cites {path}, which isn't a visible file."]
        if end < start or end - start + 1 > MAX_CITED_LINES:
            return [f"{where} cites lines {start}-{end}; cite at most {MAX_CITED_LINES} lines, in order."]
        lines = (context.source_root / PurePosixPath(path)).read_text(encoding="utf-8", errors="replace").splitlines()
        if start < 1 or end > len(lines):
            return [f"{where} cites lines {start}-{end}, but {path} has {len(lines)} lines."]
        if quote not in normalize("\n".join(lines[start - 1 : end])):
            return [f"{where}: the quote isn't in {path} lines {start}-{end}. Copy the words exactly from those lines."]
        return []
    if commit:
        message = _commit_message(commit.group("sha"), context)
        if message is None:
            return [f"{where} cites a commit that doesn't exist in the repository."]
        if context.scanner.scan_text(message):
            return [f"{where} cites a commit whose message is withheld (gitleaks flagged it)."]
        if quote not in normalize(message):
            return [f"{where}: the quote isn't in the message of commit {commit.group('sha')}."]
        return []
    return [f"{where} has no valid citation (use path#L<start>-L<end> or commit:<sha>)."]


def _commit_message(sha: str, context: ValidationContext) -> str | None:
    try:
        return run_git(["show", "-s", "--format=%B", f"{sha}^{{commit}}"], git_dir=context.mirror).decode(
            "utf-8", "replace"
        )
    except CodetrailError:
        return None


def _check_diagram(arguments: str, context: ValidationContext) -> list[str]:
    parts = arguments.split()
    if len(parts) != 2 or parts[0] not in DIAGRAM_KINDS or "=" not in parts[1]:
        return [f"The diagram placeholder '{{{{diagram{arguments}}}}}' isn't a known diagram."]
    kind, (key, value) = parts[0], parts[1].split("=", 1)
    if key != DIAGRAM_KINDS[kind]:
        return [f"The {kind} diagram takes {DIAGRAM_KINDS[kind]}=, not {key}=."]
    if kind == "imports" and not any(in_scope(path, [value]) for path in context.manifest.files):
        return [f"The diagram scope {value} holds no visible file."]
    if kind == "dependencies":
        project = context.store.entity(value)
        if project is None or project.kind is not EntityKind.PROJECT:
            return [f"The diagram project {value} isn't a project fact."]
    return []


def _check_checks(checks: Sequence[Mapping[str, Any]], context: ValidationContext) -> list[str]:
    if not 1 <= len(checks) <= 6:
        return ["Write two to four checks."]
    problems = []
    for check in checks:
        name = str(check.get("id", "?"))
        if not str(check.get("question", "")).strip():
            problems.append(f"Check {name} has no question.")
        rubric = check.get("rubric") or []
        if not rubric:
            problems.append(f"Check {name} has no rubric.")
        for point in rubric:
            for ground in point.get("grounds") or []:
                ground = str(ground)
                known_fact = context.store.entity(ground) is not None
                known_path = any(in_scope(path, [ground]) for path in context.manifest.files)
                if not (known_fact or known_path):
                    problems.append(
                        f"Check {name} is grounded in {ground}, which is neither a fact nor a visible path."
                    )
    return problems
