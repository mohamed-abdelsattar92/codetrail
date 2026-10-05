"""The page's learning routes: marks and checks (design section 8).

`POST /learn/read` marks a page or a digest read. `POST /learn/checks` sends the reader's answer to the assistant's
grading (no tools) and records the attempt; the response carries the verdict and the feedback, never the rubric.
Feedback that holds something gitleaks flags is replaced before it is stored or shown (design section 15.5), and the
call's usage is recorded. One grading runs at a time. Every route sits behind the security middleware.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

import anyio
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from codetrail.assistant import Assistant, AssistantError, GradeRequest
from codetrail.assistant.usage import UsageLog
from codetrail.config import Paths, Price
from codetrail.database import connect
from codetrail.guide import PAGE_ID, GuideRepository, Page
from codetrail.learn import VERDICTS, LearningState, page_checks
from codetrail.repo.secrets import SecretScanner


class ReadMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(max_length=200)


class CheckAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_id: str = Field(max_length=200)
    check_id: str = Field(max_length=100)
    answer: str = Field(min_length=1, max_length=20_000)


def learning_router(
    paths: Paths,
    name: str,
    assistant_for: Callable[[], Assistant],
    language_of: Callable[[], str],
    max_answer_chars: int,
    cooldown_seconds: int = 0,
    gitleaks: str = "gitleaks",
    prices: Mapping[str, Price] | None = None,
) -> APIRouter:
    router = APIRouter()
    data = paths.target_data(name)
    guide = GuideRepository(data / "guide")
    grading: dict[str, float | bool] = {"busy": False, "last": -1e18}

    def find_page(page_id: str) -> Page | None:
        return guide.read_page(page_id) if PAGE_ID.fullmatch(page_id) else None

    @router.post("/learn/read")
    def mark_read(mark: ReadMark) -> Response:
        page = find_page(mark.page_id)
        if page is None:
            return JSONResponse({"error": "That page doesn't exist."}, 404)
        connection = connect(data / "codetrail.db")
        try:
            learning = LearningState(connection)
            if page.kind == "digest":
                learning.mark_digest_read(page.id)
            else:
                learning.mark_read(page)
        finally:
            connection.close()
        return Response(status_code=204)

    @router.post("/learn/checks")
    async def answer_check(submitted: CheckAnswer) -> Response:
        page = find_page(submitted.page_id)
        check = next((item for item in page_checks(page) if item["id"] == submitted.check_id), None) if page else None
        if page is None or check is None:
            return JSONResponse({"error": "That check doesn't exist."}, 404)
        if len(submitted.answer) > max_answer_chars:
            return JSONResponse({"error": f"Answers are limited to {max_answer_chars} characters."}, 413)
        if grading["busy"]:  # claimed with no await before it, so two gradings can't both start
            return JSONResponse({"error": "Another answer is being graded."}, 429)
        if time.monotonic() - float(grading["last"]) < cooldown_seconds:
            return JSONResponse({"error": "Wait a few seconds before the next answer."}, 429)
        grading["busy"], grading["last"] = True, time.monotonic()
        try:
            language = language_of()
            request = GradeRequest(
                str(check.get("question", "")), list(check.get("rubric") or []), page.title, page.body,
                submitted.answer, language,
            )  # fmt: skip
            verdict = await assistant_for().grade(request)
        except AssistantError as error:
            return JSONResponse({"error": str(error)}, 502)
        finally:
            grading["busy"] = False
        feedback = verdict.feedback
        findings = await anyio.to_thread.run_sync(SecretScanner(gitleaks).scan_text, feedback)
        if findings:
            feedback = (
                f"The feedback was withheld: it contains something that looks like a secret ({findings[0].rule})."
            )
        if verdict.verdict not in VERDICTS:
            return JSONResponse({"error": "The grade couldn't be read, so nothing was recorded; try again."}, 502)
        connection = connect(data / "codetrail.db")
        try:
            UsageLog(connection, prices or {}).record("grade", verdict.usage)
            status = LearningState(connection).record_attempt(
                page, check, submitted.answer, verdict.verdict, feedback, language, guide.head()
            )
        finally:
            connection.close()
        return JSONResponse({"verdict": verdict.verdict, "feedback": feedback, "state": status.state})

    return router
