"""Each kind of call goes to the provider and model configured for it (design section 15.1)."""

from pathlib import Path

import pytest

from codetrail.assistant import (
    AnswerChunk,
    AssistantError,
    DigestRequest,
    GradeRequest,
    OverviewDraft,
    OverviewRequest,
    PageDraft,
    PageRequest,
    PlanRequest,
    QuestionRequest,
)
from codetrail.assistant.claude_code import ClaudeCodeAssistant
from codetrail.assistant.fake import FakeAssistant
from codetrail.assistant.local import LocalAssistant
from codetrail.assistant.routing import (
    CallBound,
    RoutedAssistant,
    build_assistant,
    build_overview_assistant,
    call_bound,
    update_call_bounds,
)
from codetrail.config import (
    ClaudeCodeSettings,
    CodexSettings,
    GenerationSettings,
    GlobalConfig,
    ModelSettings,
    Price,
    ProvidersSettings,
    TargetConfig,
)
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


def test_build_assistant_gives_programs_the_configured_output_cap(tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [])
    settings = GlobalConfig(providers=ProvidersSettings(claude_code=ClaudeCodeSettings(command=str(program.path)),
                                                        max_output_bytes=1_000_000))  # fmt: skip
    target = TargetConfig(repository=tmp_path, branch="main")
    claude = build_assistant(tmp_path / "source", settings, target).routes["write"]
    assert isinstance(claude, ClaudeCodeAssistant) and claude.max_output_bytes == 1_000_000
    overview = build_overview_assistant(tmp_path, settings, "claude-haiku-5-5", 1.0)
    assert isinstance(overview, ClaudeCodeAssistant) and overview.max_output_bytes == 1_000_000


def test_build_assistant_refuses_codex_without_the_targets_consent(tmp_path: Path) -> None:
    target = TargetConfig.model_construct(
        repository=tmp_path, branch="main", models=ModelSettings(write="codex:gpt-5.5-codex")
    )
    nothing_installed = {"PATH": str(tmp_path / "empty")}  # as on a machine without Claude Code, such as CI
    with pytest.raises(AssistantError, match="allow_codex"):  # the target's consent is checked before any program
        build_assistant(tmp_path / "source", GlobalConfig(), target, nothing_installed)


@pytest.mark.anyio
async def test_routed_overview_without_route_raises() -> None:
    main = FakeAssistant()
    with pytest.raises(AssistantError, match="overview"):
        await RoutedAssistant({"plan": main, "grade": main}).write_overview(OverviewRequest("platform", [], 250))
    writer = FakeAssistant(overviews=[OverviewDraft("Across the team.")])
    draft = await RoutedAssistant({"overview": writer}).write_overview(OverviewRequest("platform", [], 250))
    assert draft.body == "Across the team." and len(writer.requests) == 1 and main.requests == []


def test_build_overview_assistant_uses_the_models_provider(tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [])
    settings = GlobalConfig(providers=ProvidersSettings(claude_code=ClaudeCodeSettings(command=str(program.path))))
    workspace = tmp_path / "team" / "workspace"
    workspace.mkdir(parents=True)
    claude = build_overview_assistant(workspace, settings, "claude-sonnet-5-5", 0.25)
    assert isinstance(claude, ClaudeCodeAssistant)
    assert (claude.root, claude.models) == (workspace.resolve(), {"overview": "claude-sonnet-5-5"})
    assert claude.limits == GenerationSettings(max_budget_usd_per_call=0.25)  # the team's catch_up setting
    local = build_overview_assistant(workspace, settings, "local:qwen3:14b", 0.25)
    assert isinstance(local, LocalAssistant) and local.models == {"overview": "qwen3:14b"}


def test_build_overview_assistant_refuses_codex(tmp_path: Path) -> None:
    nothing_installed = {"PATH": str(tmp_path / "empty")}
    with pytest.raises(AssistantError, match="can't use Codex"):
        build_overview_assistant(tmp_path, GlobalConfig(), "codex:gpt-5.5-codex", 1.0, nothing_installed)


def test_a_calls_bound_is_the_most_it_can_cost_in_dollars() -> None:
    """Reserved in a budget before the call starts (design 6.7): Claude Code's own cap, nothing for a free local
    model, and for a priced Codex model its token limit at the model's dearest rate when that is higher."""
    settings = GlobalConfig(
        providers=ProvidersSettings(codex=CodexSettings(max_tokens_per_call=200_000)),
        prices={"gpt-dear": Price(input=5.0, output=20.0), "gpt-cheap": Price(input=0.1, output=0.4)},
    )
    assert call_bound("claude-sonnet-5-5", 0.5, settings) == CallBound("claude_code", "claude-sonnet-5-5", 0.5)
    assert call_bound("local:qwen3:14b", 0.5, settings) == CallBound("local", "qwen3:14b", 0.0)
    assert call_bound("codex:gpt-dear", 0.5, settings) == CallBound("codex", "gpt-dear", 4.0)  # 200k at $20/M
    assert call_bound("codex:gpt-cheap", 0.5, settings) == CallBound("codex", "gpt-cheap", 0.5)
    assert call_bound("codex:gpt-unpriced", 0.5, settings) == CallBound("codex", "gpt-unpriced", 0.5)


def test_an_updates_bounds_follow_its_models(tmp_path: Path) -> None:
    target = TargetConfig(repository=tmp_path, branch="main", models=ModelSettings(write="local:qwen3"),
                          generation=GenerationSettings(max_budget_usd_per_call=0.3))  # fmt: skip
    bounds = update_call_bounds(target, GlobalConfig())
    assert (bounds["plan"].cap_usd, bounds["write"].cap_usd, bounds["digest"].cap_usd) == (0.3, 0.0, 0.3)
