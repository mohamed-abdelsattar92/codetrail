"""The bridge: questions from the page, answered by Claude read-only, saved into the guide on request (design 7.3).

`POST /bridge/questions` streams newline-delimited JSON: `text` events while Claude writes, then `done` with the
answer's id and its rendered HTML, or `error`. Answers stay in memory for the session until saved. One question runs
at a time per session; closing the page cancels it. Every route sits behind the security middleware.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from codetrail.claude import AnswerChunk, Claude, ClaudeError, QuestionRequest
from codetrail.config import Paths
from codetrail.database import connect
from codetrail.facts.store import FactStore
from codetrail.generate.validate import MARKER, ValidationContext, parse_rationale, validate_page
from codetrail.guide import GuideRepository, Page
from codetrail.lock import TargetBusy, target_lock
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest
from codetrail.web.render import render_body

PAGE_CONTEXT_CHARACTERS = 12_000


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=20_000)
    page_id: str | None = Field(default=None, max_length=200)


@dataclass
class Answer:
    question: str
    language: str
    body: str
    files_read: list[str]
    commit: str
    asked_at: str
    cost_usd: float = 0.0


@dataclass
class BridgeState:
    answers: dict[str, Answer] = field(default_factory=dict)
    busy: anyio.Lock = field(default_factory=anyio.Lock)


def bridge_router(
    paths: Paths,
    name: str,
    claude_for: Callable[[], Claude],
    language_of: Callable[[], str],
    max_question_chars: int,
    max_nodes: int,
    gitleaks: str,
) -> APIRouter:
    router = APIRouter()
    state = BridgeState()
    data = paths.target_data(name)
    guide = GuideRepository(data / "guide")

    @router.post("/bridge/questions")
    async def ask(question: Question) -> Response:
        if len(question.question) > max_question_chars:
            return JSONResponse({"error": f"Questions are limited to {max_question_chars} characters."}, 413)
        manifest = SourceManifest.load(data / "source.json")
        if manifest is None:
            return JSONResponse({"error": f"Run codetrail update {name} first."}, 409)
        page = guide.read_page(question.page_id) if question.page_id else None
        if question.page_id and page is None:
            return JSONResponse({"error": "That page doesn't exist."}, 404)
        if state.busy.locked():
            return JSONResponse({"error": "Another question is still being answered."}, 429)
        request = QuestionRequest(
            target=name,
            question=question.question,
            language=language_of(),
            page_title=page.title if page else "",
            page_body=page.body[:PAGE_CONTEXT_CHARACTERS] if page else "",
            page_facts=[str(fact.get("id")) for fact in (page.meta.get("facts") or [])] if page else [],
        )
        return StreamingResponse(_stream(request, manifest.commit), media_type="application/x-ndjson")

    async def _stream(request: QuestionRequest, commit: str) -> AsyncIterator[bytes]:
        async with state.busy:
            parts: list[str] = []
            try:
                async for chunk in claude_for().answer(request):
                    if chunk.text:
                        parts.append(chunk.text)
                        yield _event({"type": "text", "text": chunk.text})
                    if chunk.done:
                        answer = Answer(request.question, request.language, "".join(parts), chunk.files_read, commit,
                                        datetime.now(UTC).isoformat(timespec="seconds"), chunk.cost_usd)  # fmt: skip
                        answer_id = uuid.uuid4().hex
                        state.answers[answer_id] = answer
                        yield _event({"type": "done", "answer_id": answer_id, "html": _html(answer.body)})
            except ClaudeError as error:
                yield _event({"type": "error", "message": str(error)})
            except Exception as error:  # details stay out of the page
                yield _event({"type": "error", "message": f"The answer failed ({type(error).__name__})."})

    def _html(body: str) -> str:
        connection = connect(data / "codetrail.db")
        try:
            segments = render_body(body, FactStore(connection), max_nodes)
        finally:
            connection.close()
        return "".join(str(segment.html) for segment in segments if segment.kind != "diagram")

    @router.post("/bridge/answers/{answer_id}/save")
    def save(answer_id: str) -> Response:
        answer = state.answers.get(answer_id)
        if answer is None:
            return JSONResponse({"error": "That answer is gone; ask again."}, 404)
        try:
            with target_lock(paths, name):
                page_id = _save(answer, data, guide, gitleaks)
        except TargetBusy:
            return JSONResponse({"error": "An update is running; save again when it finishes."}, 409)
        del state.answers[answer_id]
        return JSONResponse({"page_id": page_id})

    return router


def _save(answer: Answer, data: Any, guide: GuideRepository, gitleaks: str) -> str:
    guide.ensure()
    manifest = SourceManifest.load(data / "source.json")
    if manifest is None:
        raise ClaudeError("The sources are missing; run an update.")
    connection = connect(data / "codetrail.db")
    try:
        store = FactStore(connection)
        context = ValidationContext(data / "source", manifest, store, data / "mirror.git", SecretScanner(gitleaks))
        body = demote_unverified(answer.body, context)
    finally:
        connection.close()
    slug = re.sub(r"[^a-z0-9]+", "-", answer.question.lower()).strip("-")[:50].strip("-") or "answer"
    page_id = f"answers/{datetime.now(UTC).strftime('%Y-%m-%d')}-{slug}-{uuid.uuid4().hex[:6]}"
    meta = {
        "id": page_id, "kind": "answer", "title": answer.question[:120], "question": answer.question,
        "language": answer.language, "asked_at": answer.asked_at, "built_at": answer.commit,
        "files": [{"path": path, "blob": manifest.files[path]} for path in answer.files_read if path in manifest.files],
    }  # fmt: skip
    if guide.has_uncommitted_changes():
        raise ClaudeError("The guide has uncommitted edits; commit or discard them first.")
    guide.write_page(Page(page_id, meta, body))
    guide.commit(f"Save the answer to: {answer.question[:60]}")
    return page_id


def demote_unverified(body: str, context: ValidationContext) -> str:
    """Turns every documented block whose quote can't be verified into an inferred one (design section 7.3)."""
    lines = body.splitlines()
    for block in parse_rationale(body):
        if block.kind != "documented":
            continue
        snippet = f"> [!documented] {block.citation or ''}\n" + "\n".join(
            f"> {line}" for line in block.text.splitlines()
        )
        if validate_page(snippet, None, context):
            index = block.line - 1
            if MARKER.match(lines[index]):
                lines[index] = "> [!inferred]"
    return "\n".join(lines)


def _event(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode()


__all__ = ["AnswerChunk", "bridge_router", "demote_unverified"]
