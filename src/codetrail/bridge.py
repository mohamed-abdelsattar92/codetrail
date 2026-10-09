"""The bridge: questions from the page, answered read-only by the assistant, saved to the guide on request (7.3).

`POST /bridge/questions` streams newline-delimited JSON: `text` events while the assistant writes, then `done` with
the answer's id and its rendered HTML, or `error`. The whole answer is scanned for secrets before `done`; one that
holds something gitleaks flags ends with `error` and isn't kept (design section 15.5). Its usage is recorded.
Answers stay in memory for the session until saved, at most `max_session_answers` of them, and `GET /bridge/answers`
lists them for the page's Ask panel. One question runs at a time per session; closing the page
cancels it. Every route sits behind the security middleware.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse
from markupsafe import escape
from pydantic import BaseModel, ConfigDict, Field

from codetrail.assistant import AnswerChunk, Assistant, AssistantError, QuestionRequest, Usage
from codetrail.assistant.usage import UsageLog
from codetrail.config import Paths, Price, ToolsSettings
from codetrail.database import connect
from codetrail.errors import CodetrailError
from codetrail.facts.store import FactStore
from codetrail.generate.validate import MARKER, ValidationContext, parse_rationale, validate_page
from codetrail.guide import PAGE_ID, GuideRepository, Page
from codetrail.lock import TargetBusy, target_lock
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest
from codetrail.web.diagrams import available_diagrams
from codetrail.web.i18n import Language
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
    answering_since: float | None = None  # claimed in the request itself, so simultaneous questions are refused
    holder: str | None = None
    abandon_after_seconds: float = 3600.0  # a slot held this long is treated as abandoned

    def claim(self) -> str | None:
        """Claims the one question slot and returns its token; a slot held well past a call's limit is abandoned."""
        now = time.monotonic()
        if self.answering_since is not None and now - self.answering_since < self.abandon_after_seconds:
            return None
        self.answering_since, self.holder = now, uuid.uuid4().hex
        return self.holder

    def release(self, token: str) -> None:
        """Releases the slot only for the claim that still holds it."""
        if self.holder == token:
            self.answering_since, self.holder = None, None


def bridge_router(
    paths: Paths,
    name: str,
    assistant_for: Callable[[], Assistant],
    language_of: Callable[[], Language],
    max_question_chars: int,
    max_nodes: int,
    tools: ToolsSettings,
    prices: Mapping[str, Price],
    call_timeout_seconds: float,
    max_session_answers: int = 20,
) -> APIRouter:
    router = APIRouter()
    state = BridgeState(abandon_after_seconds=call_timeout_seconds + 60)
    scanner = SecretScanner(tools)
    data = paths.target_data(name)
    guide = GuideRepository(data / "guide")

    @router.post("/bridge/questions")
    async def ask(question: Question) -> Response:
        language = language_of()
        _ = language.translations.gettext
        if len(question.question) > max_question_chars:
            limit = _("Questions are limited to %(count)s characters.") % {"count": max_question_chars}
            return JSONResponse({"error": limit}, 413)
        manifest = SourceManifest.load(data / "source.json")
        if manifest is None:
            return JSONResponse({"error": _("Run codetrail update %(target)s first.") % {"target": name}}, 409)
        if question.page_id and not PAGE_ID.fullmatch(question.page_id):
            return JSONResponse({"error": _("That page doesn't exist.")}, 404)
        page = guide.read_page(question.page_id) if question.page_id else None
        if question.page_id and page is None:
            return JSONResponse({"error": _("That page doesn't exist.")}, 404)
        diagrams = _available_diagrams()  # before the claim: nothing between claim and stream may fail
        token = state.claim()  # no await before this point, so two requests can't both pass
        if token is None:
            return JSONResponse({"error": _("Another question is still being answered.")}, 429)
        request = QuestionRequest(
            target=name,
            question=question.question,
            language=language.code,
            page_title=page.title if page else "",
            page_body=page.body[:PAGE_CONTEXT_CHARACTERS] if page else "",
            page_facts=[str(fact.get("id")) for fact in (page.meta.get("facts") or [])] if page else [],
            diagrams=diagrams,
        )
        return StreamingResponse(_stream(request, manifest.commit, token), media_type="application/x-ndjson")

    async def _stream(request: QuestionRequest, commit: str, token: str) -> AsyncIterator[bytes]:
        try:
            _ = language_of().translations.gettext  # inside the try: the claim is released whatever fails
            parts: list[str] = []
            try:
                async for chunk in assistant_for().answer(request):
                    if chunk.text:
                        parts.append(chunk.text)
                        if not chunk.done:  # streamed text; a buffered answer arrives with done, after the scan
                            yield _event({"type": "text", "text": chunk.text})
                    if chunk.done:
                        findings = await anyio.to_thread.run_sync(scanner.scan_text, "".join(parts))
                        cost = _record(chunk)
                        if findings:
                            yield _event(
                                {
                                    "type": "error",
                                    "message": _(
                                        "The answer was withheld: it contains something that looks like a secret "
                                        "(%(rule)s)."
                                    )
                                    % {"rule": findings[0].rule},
                                }
                            )
                            return
                        answer = Answer(request.question, request.language, "".join(parts), chunk.files_read, commit,
                                        datetime.now(UTC).isoformat(timespec="seconds"), chunk.cost_usd)  # fmt: skip
                        answer_id = uuid.uuid4().hex
                        state.answers[answer_id] = answer
                        while len(state.answers) > max_session_answers:  # the oldest unsaved answer goes first
                            del state.answers[next(iter(state.answers))]
                        used = {"tokens": _tokens(chunk.usage), "cost_usd": round(cost, 4)}
                        yield _event(
                            {"type": "done", "answer_id": answer_id, "html": _html(answer.body), "usage": used}
                        )
            except AssistantError as error:
                yield _event({"type": "error", "message": str(error)})
            except Exception as error:  # details stay out of the page
                failed = _("The answer failed (%(error)s).") % {"error": type(error).__name__}
                yield _event({"type": "error", "message": failed})
        finally:
            state.release(token)

    def _record(chunk: AnswerChunk) -> float:
        connection = connect(data / "codetrail.db")
        try:
            return UsageLog(connection, prices).record("answer", chunk.usage) or chunk.cost_usd
        finally:
            connection.close()

    def _available_diagrams() -> list[str]:
        """The diagrams an answer may place; none if they can't be read, since the answer works without them."""
        try:
            connection = connect(data / "codetrail.db")
            try:
                return available_diagrams(FactStore(connection))
            finally:
                connection.close()
        except Exception:  # an optional extra: a locked or newer database mustn't stop the question
            return []

    def _html(body: str) -> str:
        connection = connect(data / "codetrail.db")
        try:
            segments = render_body(body, FactStore(connection), max_nodes)
        finally:
            connection.close()
        parts = []
        for segment in segments:
            if segment.kind == "diagram" and segment.diagram is not None:  # drawn by the page's script, from facts
                parts.append(f'<figure class="diagram-block" lang="en" dir="ltr"><pre class="diagram">'
                             f"{escape(segment.diagram.mermaid)}</pre></figure>")  # fmt: skip
            else:
                parts.append(str(segment.html))
        return "".join(parts)

    @router.get("/bridge/answers")
    def session_answers() -> Response:
        """This session's unsaved answers, oldest first, rendered and sanitized, for the Ask panel (design 16.4)."""
        listed = [
            {"id": answer_id, "question": answer.question, "html": _html(answer.body), "asked_at": answer.asked_at}
            for answer_id, answer in list(state.answers.items())
        ]
        return JSONResponse({"answers": listed}, headers={"Cache-Control": "no-store"})

    @router.post("/bridge/answers/{answer_id}/save")
    def save(answer_id: str) -> Response:
        _ = language_of().translations.gettext
        answer = state.answers.get(answer_id)
        if answer is None:
            return JSONResponse({"error": _("That answer is gone; ask again.")}, 404)
        try:
            with target_lock(paths, name):
                page_id = _save(answer, data, guide, tools)
        except TargetBusy:
            return JSONResponse({"error": _("An update is running; save again when it finishes.")}, 409)
        except CodetrailError as error:
            return JSONResponse({"error": str(error)}, 409)
        del state.answers[answer_id]
        return JSONResponse({"page_id": page_id})

    return router


def _tokens(usage: Usage) -> int:
    return usage.input_tokens + usage.cached_input_tokens + usage.output_tokens


def _save(answer: Answer, data: Any, guide: GuideRepository, tools: ToolsSettings) -> str:
    guide.ensure()
    manifest = SourceManifest.load(data / "source.json")
    if manifest is None:
        raise AssistantError("The sources are missing; run an update.")
    connection = connect(data / "codetrail.db")
    try:
        store = FactStore(connection)
        context = ValidationContext(data / "source", manifest, store, data / "mirror.git", SecretScanner(tools))
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
        raise AssistantError("The guide has uncommitted edits; commit or discard them first.")
    try:
        guide.write_page(Page(page_id, meta, body))
        guide.commit(f"Save the answer to: {answer.question[:60]}")
    except BaseException:
        guide.discard()  # never leave the guide dirty, or every update would refuse to start
        raise
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
