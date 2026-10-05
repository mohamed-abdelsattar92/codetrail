"""A fake Claude for tests: it replays scripted drafts and records every request it was given."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from codetrail.claude import (
    AnswerChunk,
    ClaudeError,
    DigestDraft,
    DigestRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
    QuestionRequest,
)

PageScript = Callable[[PageRequest], PageDraft]


@dataclass
class FakeClaude:
    plans: list[PlanDraft] = field(default_factory=list)
    pages: dict[str, list[PageDraft | Exception]] = field(default_factory=dict)
    page_writer: PageScript | None = None
    digests: list[DigestDraft] = field(default_factory=list)
    answers: list[list[AnswerChunk] | Exception] = field(default_factory=list)
    requests: list[PlanRequest | PageRequest | DigestRequest | QuestionRequest] = field(default_factory=list)

    async def plan(self, request: PlanRequest) -> PlanDraft:
        self.requests.append(request)
        if not self.plans:
            raise ClaudeError("The fake has no plan scripted.")
        return self.plans.pop(0)

    async def write_page(self, request: PageRequest) -> PageDraft:
        self.requests.append(request)
        scripted = self.pages.get(request.page_id)
        if scripted:
            draft = scripted.pop(0)
            if isinstance(draft, Exception):
                raise draft
            return draft
        if self.page_writer is not None:
            return self.page_writer(request)
        raise ClaudeError(f"The fake has no page scripted for {request.page_id}.")

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        self.requests.append(request)
        if not self.digests:
            return DigestDraft(title="Changes", body="Things changed.")
        return self.digests.pop(0)

    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        self.requests.append(request)
        scripted = self.answers.pop(0) if self.answers else [AnswerChunk("An answer."), AnswerChunk(done=True)]
        if isinstance(scripted, Exception):
            raise scripted
        for chunk in scripted:
            yield chunk
