"""The interface Codetrail needs from an assistant, shaped by Codetrail's tasks, not by any vendor (design 6.9, 15).

Each provider (Claude Code, Codex, a local model) has its own adapter, the only code that knows it; tests use the fake.
Every draft carries the call's usage: its tokens, and its cost when the provider or the configured prices give one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from codetrail.errors import CodetrailError


class AssistantError(CodetrailError):
    """The assistant couldn't complete a task: an error, a limit reached, or no usable answer."""


@dataclass(frozen=True)
class PlanWindow:
    """How much of a subscription's usage window is used (0 to 1), and when it resets (seconds since the epoch)."""

    window: str  # "five_hour", "seven_day"...
    utilization: float
    resets_at: int


@dataclass(frozen=True)
class Usage:
    """What one call used: tokens, and its cost in dollars when known (the provider's figure, or tokens x prices).

    `input_tokens` counts input not read from a cache; `cached_input_tokens` counts input read from one.
    """

    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    plan_windows: tuple[PlanWindow, ...] = ()


@dataclass(frozen=True)
class PlanRequest:
    target: str
    facts: str  # a rolled-up summary of the facts
    existing_outline: str  # YAML, empty on the first run
    uncovered: list[str] = field(default_factory=list)  # fact ids in no page's scope
    paths_only: bool = False  # propose guided paths through the existing pages, and no new pages


@dataclass(frozen=True)
class PlanDraft:
    pages: list[dict[str, Any]]
    paths: list[dict[str, Any]] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    usage: Usage = field(default_factory=Usage)


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
    usage: Usage = field(default_factory=Usage)


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
    usage: Usage = field(default_factory=Usage)


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
    """A piece of a streamed answer; the last one has `done` set, with the files read, the cost and the usage."""

    text: str = ""
    done: bool = False
    files_read: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class GradeRequest:
    question: str
    rubric: list[dict[str, Any]]
    page_title: str
    page_body: str
    answer: str
    language: str


@dataclass(frozen=True)
class Verdict:
    verdict: str  # "pass", "partial" or "fail"; anything else is an error
    missed: list[str] = field(default_factory=list)
    feedback: str = ""
    cost_usd: float = 0.0
    usage: Usage = field(default_factory=Usage)


class Assistant(Protocol):
    async def plan(self, request: PlanRequest) -> PlanDraft: ...

    async def write_page(self, request: PageRequest) -> PageDraft: ...

    async def write_digest(self, request: DigestRequest) -> DigestDraft: ...

    def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]: ...

    async def grade(self, request: GradeRequest) -> Verdict: ...
