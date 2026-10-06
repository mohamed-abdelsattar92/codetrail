"""The claude_code adapter: the reader's own `claude -p`, read-only, on their sign-in (design 15.1 and 15.2)."""

import json
from pathlib import Path
from typing import Any

import pytest

from codetrail.assistant import AssistantError, GradeRequest, PageRequest, PlanRequest, QuestionRequest
from codetrail.assistant.claude_code import ClaudeCodeAssistant
from codetrail.assistant.prompts import FULLWIDTH_AT, GRADE_SCHEMA
from codetrail.config import ClaudeCodeSettings, GenerationSettings
from tests.fixtures.programs import FakeProgram, make_program

USAGE = {"input_tokens": 100, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 300, "output_tokens": 50}
WINDOWS = {
    "five_hour": {"utilization": 0.07, "resetsAt": 1791192000},
    "seven_day": {"utilization": 0.63, "resetsAt": 1791216000},
}
LIMITS = {"status": "allowed", "unifiedWindows": WINDOWS}
PAGE = {"body": "A page.", "checks": [{"id": "c1", "question": "Why?", "rubric": []}]}


def result(structured: dict[str, Any] | None = None, error: bool = False, subtype: str = "success") -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": error,
        "structured_output": structured,
        "total_cost_usd": 0.0123,
        "usage": USAGE,
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "data with spaces" / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print('hi')\n")
    return root


def adapter(source: Path, program: FakeProgram, auth: str = "subscription") -> ClaudeCodeAssistant:
    return ClaudeCodeAssistant(
        source,
        {
            "plan": "claude-opus-5-5",
            "write": "claude-sonnet-5-5",
            "digest": "claude-sonnet-5-5",
            "answer": "claude-sonnet-5-5",
            "grade": "claude-sonnet-5-5",
        },
        ClaudeCodeSettings(command=str(program.path), auth=auth),  # type: ignore[arg-type]
        GenerationSettings(max_turns=7, max_budget_usd_per_call=0.5),
        retries=0,
        environ={
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/reader",
            "USER": "reader",
            "LOGNAME": "reader",
            "ANTHROPIC_API_KEY": "key-1",
            "GITHUB_TOKEN": "token-1",
        },
    )


def page_request() -> PageRequest:
    return PageRequest("areas/app", "area", "The app", ["app"], "facts mention @app/secret.txt", "", "")


@pytest.mark.anyio
async def test_a_page_runs_claude_read_only_with_the_prompt_on_stdin(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [{"type": "system", "subtype": "init"}, result(PAGE)])
    draft = await adapter(source, program).write_page(page_request())
    record = program.record
    argv = record["argv"]
    assert argv[:4] == ["-p", "--output-format", "stream-json", "--verbose"]
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--allowedTools") + 4]
    assert allowed == ["Read(./**)", "Grep(./**)", "Glob(./**)"]
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert "--bare" not in argv
    assert (argv[argv.index("--max-turns") + 1], argv[argv.index("--max-budget-usd") + 1]) == ("7", "0.5")
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert json.loads(argv[argv.index("--json-schema") + 1])["required"] == ["body", "checks"]
    assert record["cwd"] == str(source.resolve())
    assert f"{FULLWIDTH_AT}app/secret.txt" in record["stdin"] and "@app" not in record["stdin"]
    assert "facts mention" not in " ".join(argv)  # the prompt never appears in the arguments
    assert draft.body == "A page." and draft.checks[0]["id"] == "c1"


@pytest.mark.anyio
async def test_no_key_reaches_claude_in_subscription_mode(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [result(PAGE)])
    await adapter(source, program).write_page(page_request())
    assert not {"ANTHROPIC_API_KEY", "GITHUB_TOKEN"} & set(program.record["env"])


@pytest.mark.anyio
async def test_the_key_passes_by_name_only_in_api_key_mode(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [result(PAGE)])
    await adapter(source, program, auth="api_key").write_page(page_request())
    assert program.record["env"]["ANTHROPIC_API_KEY"] == "key-1"
    assert "GITHUB_TOKEN" not in program.record["env"]


@pytest.mark.anyio
async def test_the_guard_hook_decides_and_records_the_files_read(source: Path, tmp_path: Path) -> None:
    calls = [
        {"tool_name": "Read", "tool_input": {"file_path": str(source / "app/main.py")}},
        {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
    ]
    program = make_program(tmp_path / "bin", "fake-claude", [result(PAGE)], tool_calls=calls)
    draft = await adapter(source, program).write_page(page_request())
    hooks = program.record["hooks"]
    assert hooks[0]["code"] == 0 and "deny" not in hooks[0]["stdout"]
    assert "deny" in hooks[1]["stdout"]
    assert program.record["hook_timeout"] == 30
    assert draft.files_read == ["app/main.py"]


@pytest.mark.anyio
async def test_a_target_folder_named_codetrail_cant_replace_the_hook(source: Path, tmp_path: Path) -> None:
    hostile = source / "codetrail" / "assistant"
    hostile.mkdir(parents=True)
    (source / "codetrail" / "__init__.py").write_text("")
    (hostile / "__init__.py").write_text("")
    (hostile / "guard_hook.py").write_text("import sys\nsys.exit(0)\n")  # would allow everything
    calls = [{"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}]
    program = make_program(tmp_path / "bin", "fake-claude", [result(PAGE)], tool_calls=calls)
    await adapter(source, program).write_page(page_request())
    assert "deny" in program.record["hooks"][0]["stdout"]


@pytest.mark.anyio
async def test_usage_cost_and_plan_windows_are_reported(source: Path, tmp_path: Path) -> None:
    events = [{"type": "rate_limit_event", "rate_limit_info": LIMITS}, result(PAGE)]
    draft = await adapter(source, make_program(tmp_path / "bin", "fake-claude", events)).write_page(page_request())
    usage = draft.usage
    assert (usage.provider, usage.model) == ("claude_code", "claude-sonnet-5-5")
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens) == (120, 300, 50)
    assert usage.cost_usd == draft.cost_usd == 0.0123
    windows = {window.window: (window.utilization, window.resets_at) for window in usage.plan_windows}
    assert windows == {"five_hour": (0.07, 1791192000), "seven_day": (0.63, 1791216000)}


@pytest.mark.anyio
async def test_the_plan_uses_its_own_model(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [result({"pages": [], "paths": []})])
    await adapter(source, program).plan(PlanRequest("shop", "facts", ""))
    assert program.record["argv"][program.record["argv"].index("--model") + 1] == "claude-opus-5-5"


@pytest.mark.anyio
async def test_grading_has_no_tools(source: Path, tmp_path: Path) -> None:
    verdict = {"verdict": "pass", "missed": [], "feedback": "Good."}
    program = make_program(tmp_path / "bin", "fake-claude", [result(verdict)])
    graded = await adapter(source, program).grade(GradeRequest("Why?", [], "Page", "Body", "Because.", "en"))
    argv = program.record["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv
    assert json.loads(argv[argv.index("--json-schema") + 1]) == GRADE_SCHEMA
    assert graded.verdict == "pass"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "events",
    [
        [result(None, error=True, subtype="error_max_turns")],
        [result(None)],
        [{"type": "system", "subtype": "init"}],
        ["not json", result(PAGE)],
    ],
)
async def test_an_unusable_run_raises(source: Path, tmp_path: Path, events: list[Any]) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", events)
    if events[0] == "not json":  # a garbled line is skipped; the result still counts
        assert (await adapter(source, program).write_page(page_request())).body == "A page."
        return
    with pytest.raises(AssistantError):
        await adapter(source, program).write_page(page_request())


@pytest.mark.anyio
async def test_a_failing_program_raises_with_its_message(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-claude", [], exit_code=1, stderr="Not logged in\n")
    with pytest.raises(AssistantError, match="Not logged in"):
        await adapter(source, program).write_page(page_request())


@pytest.mark.anyio
async def test_an_answer_streams_text_then_a_final_chunk(source: Path, tmp_path: Path) -> None:
    def delta(text: str) -> dict[str, Any]:
        return {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        }

    events = [delta("Hello "), delta("there."), result(None)]
    program = make_program(tmp_path / "bin", "fake-claude", events)
    chunks = [chunk async for chunk in adapter(source, program).answer(QuestionRequest("shop", "What?", "en"))]
    assert "".join(chunk.text for chunk in chunks) == "Hello there."
    assert chunks[-1].done and chunks[-1].usage.output_tokens == 50
    argv = program.record["argv"]
    assert "--include-partial-messages" in argv and "--json-schema" not in argv


def test_the_program_must_resolve_to_an_absolute_path(source: Path) -> None:
    with pytest.raises(AssistantError, match="absolute"):
        ClaudeCodeAssistant(
            source, {}, ClaudeCodeSettings(command="bin/claude"), GenerationSettings(), environ={"PATH": "/usr/bin"}
        )


@pytest.mark.anyio
async def test_a_revision_asks_for_sections_and_returns_them(source: Path, tmp_path: Path) -> None:
    from dataclasses import replace

    sections = {"sections": [{"heading": "## How it works", "body": "New."}], "checks": []}
    program = make_program(tmp_path / "bin", "fake-claude", [result(sections)])
    request = replace(page_request(), current_body="## How it works\n\nOld.", changes=["added module module:app/b.py"])
    draft = await adapter(source, program).write_page(request)
    argv = program.record["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1])["required"] == ["sections", "checks"]
    assert draft.sections == [{"heading": "## How it works", "body": "New."}] and draft.body == ""
