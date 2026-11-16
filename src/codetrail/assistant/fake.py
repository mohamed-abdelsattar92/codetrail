"""A fake assistant for tests: it replays scripted drafts and records every request it was given."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from codetrail.assistant import (
    AnswerChunk,
    AssistantError,
    DigestDraft,
    DigestRequest,
    GradeRequest,
    OverviewDraft,
    OverviewRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
    QuestionRequest,
    Verdict,
)

PageScript = Callable[[PageRequest], PageDraft]


@dataclass
class FakeAssistant:
    plans: list[PlanDraft | Exception] = field(default_factory=list)
    pages: dict[str, list[PageDraft | Exception]] = field(default_factory=dict)
    page_writer: PageScript | None = None
    digests: list[DigestDraft | Exception] = field(default_factory=list)
    answers: list[list[AnswerChunk] | Exception] = field(default_factory=list)
    verdicts: list[Verdict | Exception] = field(default_factory=list)
    overviews: list[OverviewDraft | BaseException] = field(default_factory=list)  # BaseException: a cancellation
    requests: list[PlanRequest | PageRequest | DigestRequest | QuestionRequest | GradeRequest | OverviewRequest] = (
        field(default_factory=list)
    )

    async def plan(self, request: PlanRequest) -> PlanDraft:
        self.requests.append(request)
        if not self.plans:
            raise AssistantError("The fake has no plan scripted.")
        plan = self.plans.pop(0)
        if isinstance(plan, Exception):
            raise plan
        return plan

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
        raise AssistantError(f"The fake has no page scripted for {request.page_id}.")

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        self.requests.append(request)
        if not self.digests:
            return DigestDraft(title="Changes", body="Things changed.")
        digest = self.digests.pop(0)
        if isinstance(digest, Exception):
            raise digest
        return digest

    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        self.requests.append(request)
        scripted = self.answers.pop(0) if self.answers else [AnswerChunk("An answer."), AnswerChunk(done=True)]
        if isinstance(scripted, Exception):
            raise scripted
        for chunk in scripted:
            yield chunk

    async def grade(self, request: GradeRequest) -> Verdict:
        self.requests.append(request)
        scripted = self.verdicts.pop(0) if self.verdicts else Verdict("pass", [], "Well explained.")
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    async def write_overview(self, request: OverviewRequest) -> OverviewDraft:
        self.requests.append(request)
        draft = self.overviews.pop(0) if self.overviews else OverviewDraft("Across the team, things changed.")
        if isinstance(draft, BaseException):
            raise draft
        return draft
