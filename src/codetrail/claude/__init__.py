"""The interface Codetrail needs from Claude, shaped by Codetrail's tasks, not by any vendor (design section 6.9).

The Agent SDK adapter is the only code that imports the SDK; tests use the fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from codetrail.errors import CodetrailError


class ClaudeError(CodetrailError):
    """Claude couldn't complete a task: an error, a limit reached, or no usable answer."""


@dataclass(frozen=True)
class PlanRequest:
    target: str
    facts: str  # a rolled-up summary of the facts
    existing_outline: str  # YAML, empty on the first run
    uncovered: list[str] = field(default_factory=list)  # fact ids in no page's scope


@dataclass(frozen=True)
class PlanDraft:
    pages: list[dict[str, Any]]
    files_read: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


@dataclass(frozen=True)
class PageRequest:
    page_id: str
    kind: str
    title: str
    scope_paths: list[str]
    facts: str
    decisions: str
    history: str
    problems: list[str] = field(default_factory=list)  # from a failed validation, for the retry
    previous_body: str = ""


@dataclass(frozen=True)
class PageDraft:
    body: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


@dataclass(frozen=True)
class DigestRequest:
    target: str
    commits: str
    fact_changes: str
    pages_changed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DigestDraft:
    title: str
    body: str
    files_read: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


@dataclass(frozen=True)
class QuestionRequest:
    target: str
    question: str
    language: str  # a validated, installed language code
    page_title: str = ""
    page_body: str = ""
    page_facts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerChunk:
    """A piece of a streamed answer; the last one has `done` set, with the files read and the cost."""

    text: str = ""
    done: bool = False
    files_read: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


class Claude(Protocol):
    async def plan(self, request: PlanRequest) -> PlanDraft: ...

    async def write_page(self, request: PageRequest) -> PageDraft: ...

    async def write_digest(self, request: DigestRequest) -> DigestDraft: ...

    def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]: ...
