"""Each kind of call goes to the provider and model configured for it (design section 15.1)."""

from pathlib import Path

import pytest

from codetrail.assistant import (
    AnswerChunk,
    AssistantError,
    DigestRequest,
    GradeRequest,
    PageDraft,
    PageRequest,
    PlanRequest,
    QuestionRequest,
)
from codetrail.assistant.claude_code import ClaudeCodeAssistant
from codetrail.assistant.fake import FakeAssistant
from codetrail.assistant.local import LocalAssistant
from codetrail.assistant.routing import RoutedAssistant, build_assistant
from codetrail.config import ClaudeCodeSettings, GlobalConfig, ModelSettings, ProvidersSettings, TargetConfig
from tests.fixtures.programs import make_program


@pytest.mark.anyio
async def test_each_call_goes_to_its_route() -> None:
    main = FakeAssistant(page_writer=lambda request: PageDraft("A page."))
    answers = FakeAssistant(answers=[[AnswerChunk("local"), AnswerChunk(done=True)]])
    routed = RoutedAssistant({"plan": main, "write": main, "digest": main, "answer": answers, "grade": main})
    await routed.write_page(PageRequest("areas/x", "area", "X", [], "", "", ""))
    await routed.write_digest(DigestRequest("shop", "", ""))
    await routed.grade(GradeRequest("Q", [], "T", "B", "A", "en"))
    chunks = [chunk async for chunk in routed.answer(QuestionRequest("shop", "Q?", "en"))]
    assert chunks[0].text == "local"
    assert len(main.requests) == 3 and len(answers.requests) == 1
    with pytest.raises(AssistantError):  # the fake has no plan scripted: the call reached it
        await routed.plan(PlanRequest("shop", "", ""))


def test_build_assistant_uses_one_adapter_per_provider(tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [])
    settings = GlobalConfig(providers=ProvidersSettings(claude_code=ClaudeCodeSettings(command=str(program.path))))
    target = TargetConfig(repository=tmp_path, branch="main", models=ModelSettings(answer="local:qwen3:14b"))
    routed = build_assistant(tmp_path / "source", settings, target)
    assert isinstance(routed.routes["plan"], ClaudeCodeAssistant)
    assert routed.routes["plan"] is routed.routes["write"]
    assert isinstance(routed.routes["answer"], LocalAssistant)
    assert routed.routes["plan"].models["plan"] == "claude-opus-5-5"
    assert routed.routes["answer"].models["answer"] == "qwen3:14b"


def test_build_assistant_refuses_codex_without_the_targets_consent(tmp_path: Path) -> None:
    target = TargetConfig.model_construct(
        repository=tmp_path, branch="main", models=ModelSettings(write="codex:gpt-5.5-codex")
    )
    with pytest.raises(AssistantError, match="allow_codex"):
        build_assistant(tmp_path / "source", GlobalConfig(), target)
