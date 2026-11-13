"""The codex adapter: the reader's own `codex exec`, read-only, isolated from their config (design 15.1, 15.5)."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import pytest

from codetrail.assistant import (
    AssistantError,
    GradeRequest,
    OverviewRequest,
    PageRequest,
    PaidCallFailed,
    QuestionRequest,
    runner,
)
from codetrail.assistant.codex import CodexAssistant
from codetrail.assistant.prompts import FULLWIDTH_AT
from codetrail.config import CodexSettings, GenerationSettings
from tests.fixtures.programs import FakeProgram, make_program

PAGE = {"body": "A page.", "checks": [], "brief": "B"}


def message(text: str) -> dict[str, Any]:
    return {"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": text}}


def turn(input_tokens: int = 1000, cached: int = 400, output: int = 80) -> dict[str, Any]:
    usage = {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output}
    return {"type": "turn.completed", "usage": usage}


def command_item(command: str) -> dict[str, Any]:
    return {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": command,
            "exit_code": 0,
            "status": "completed",
        },
    }


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print('hi')\n")
    return root


def adapter(
    source: Path,
    program: FakeProgram,
    auth: str = "subscription",
    max_tokens: int = 400_000,
    model: str = "gpt-5.5-codex",
) -> CodexAssistant:
    return CodexAssistant(
        source,
        {"write": model, "answer": model},
        CodexSettings(command=str(program.path), auth=auth, max_tokens_per_call=max_tokens),  # type: ignore[arg-type]
        GenerationSettings(),
        retries=0,
        environ={
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/reader",
            "USER": "reader",
            "LOGNAME": "reader",
            "SHELL": "/bin/zsh",
            "OPENAI_API_KEY": "key-2",
            "GITHUB_TOKEN": "token-1",
        },
    )


def page_request() -> PageRequest:
    return PageRequest("areas/app", "area", "The app", ["app"], "see @app/main.py", "", "")


@pytest.mark.anyio
async def test_a_page_runs_codex_read_only_and_isolated(source: Path, tmp_path: Path) -> None:
    events = [{"type": "thread.started"}, command_item("bash -lc 'cat app/main.py'"), message(json.dumps(PAGE)), turn()]
    program = make_program(tmp_path / "bin", "fake-codex", events)
    draft = await adapter(source, program).write_page(page_request())
    record = program.record
    argv = record["argv"]
    assert argv[:2] == ["exec", "-"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    for flag in ("--json", "--ephemeral", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("-C") + 1] == str(source.resolve())
    assert argv[argv.index("-m") + 1] == "gpt-5.5-codex"
    overrides = [argv[index + 1] for index, value in enumerate(argv) if value == "-c"]
    assert overrides == [
        "mcp_servers={}",
        "notify=[]",
        "project_doc_max_bytes=0",
        'shell_environment_policy.inherit="none"',
        'web_search="disabled"',  # whatever the reader's config.toml says: nothing read can leave in a search
        "tools.web_search=false",  # the older key, which a reader's config.toml may still set
        # the commands Codex runs start from an empty environment, so they get git's guard against a bare repository
        # committed at source/'s root here (section 15.5)
        'shell_environment_policy.set.GIT_CONFIG_COUNT="1"',
        'shell_environment_policy.set.GIT_CONFIG_KEY_0="safe.bareRepository"',
        'shell_environment_policy.set.GIT_CONFIG_VALUE_0="explicit"',
    ]
    assert record["schema"]["required"] == ["body", "checks", "brief"]
    assert f"{FULLWIDTH_AT}app/main.py" in record["stdin"]
    assert record["cwd"] == str(source.resolve())
    assert (draft.body, draft.files_read, draft.brief) == ("A page.", ["app/main.py"], "B")
    assert (draft.usage.provider, draft.usage.input_tokens, draft.usage.cached_input_tokens) == ("codex", 600, 400)
    assert draft.usage.cost_usd is None


@pytest.mark.anyio
async def test_codex_gets_an_empty_home_a_plain_shell_and_no_key(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", [message(json.dumps(PAGE)), turn()])
    await adapter(source, program).write_page(page_request())
    environment = program.record["env"]
    assert environment["SHELL"] == "/bin/sh"
    assert environment["CODEX_HOME"] == "/Users/reader/.codex"
    assert environment["HOME"] != "/Users/reader" and not Path(environment["HOME"]).exists()  # removed after the call
    assert not {"USER", "LOGNAME", "OPENAI_API_KEY", "GITHUB_TOKEN"} & set(environment)


@pytest.mark.anyio
async def test_its_keys_pass_only_in_api_key_mode(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", [message(json.dumps(PAGE)), turn()])
    await adapter(source, program, auth="api_key").write_page(page_request())
    assert program.record["env"]["OPENAI_API_KEY"] == "key-2"


@pytest.mark.anyio
async def test_no_model_means_codex_chooses(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", [message(json.dumps(PAGE)), turn()])
    await adapter(source, program, model="").write_page(page_request())
    assert "-m" not in program.record["argv"]


@pytest.mark.anyio
async def test_passing_the_token_limit_stops_the_call(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", [turn(5000, 0, 100), message(json.dumps(PAGE)), turn()])
    with pytest.raises(AssistantError, match="token limit"):
        await adapter(source, program, max_tokens=1000).write_page(page_request())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("events", "exit_code"),
    [
        ([turn(5000, 0, 100), message(json.dumps(PAGE))], 0),  # past the token limit
        ([turn(), {"type": "turn.failed", "error": {"message": "usage limit reached"}}], 1),
        ([message("not json"), turn()], 0),
    ],
)
async def test_a_failed_call_carries_the_tokens_it_counted(
    source: Path, tmp_path: Path, events: list[Any], exit_code: int
) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", events, exit_code=exit_code)
    with pytest.raises(PaidCallFailed) as raised:
        await adapter(source, program, max_tokens=4000).write_page(page_request())
    usage = raised.value.usage
    assert usage.provider == "codex" and usage.input_tokens + usage.output_tokens > 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "events",
    [
        [message("not json"), turn()],
        [turn()],
        [{"type": "turn.failed", "error": {"message": "usage limit reached"}}],
        [{"type": "error", "message": "stream disconnected"}],
    ],
)
async def test_an_unusable_run_raises(source: Path, tmp_path: Path, events: list[Any]) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", events)
    with pytest.raises(AssistantError):
        await adapter(source, program).write_page(page_request())


@pytest.mark.anyio
@pytest.mark.parametrize("event_type", ["error", "turn.failed"])
async def test_structured_failure_takes_precedence_over_stderr(source: Path, tmp_path: Path, event_type: str) -> None:
    detail = "Invalid output schema: missing scope_kinds"
    event = (
        {"type": "error", "message": detail}
        if event_type == "error"
        else {"type": "turn.failed", "error": {"message": detail}}
    )
    program = make_program(
        tmp_path / "bin",
        "fake-codex",
        [event],
        exit_code=1,
        stderr="skills scan reached its traversal limit",
    )
    with pytest.raises(AssistantError, match="missing scope_kinds"):
        await adapter(source, program).write_page(page_request())


@pytest.mark.anyio
async def test_an_answer_is_buffered_until_codex_finishes(source: Path, tmp_path: Path) -> None:
    events = [message("Hello there."), turn()]
    program = make_program(tmp_path / "bin", "fake-codex", events)
    chunks = [chunk async for chunk in adapter(source, program).answer(QuestionRequest("shop", "What?", "en"))]
    assert [chunk.text for chunk in chunks] == ["Hello there."]
    assert chunks[-1].done and chunks[-1].usage.output_tokens == 80
    assert "--output-schema" not in program.record["argv"]


@pytest.mark.anyio
async def test_a_revision_returns_its_sections(source: Path, tmp_path: Path) -> None:
    from dataclasses import replace

    sections = {"sections": [{"heading": "## A", "body": "New."}], "checks": []}
    program = make_program(tmp_path / "bin", "fake-codex", [message(json.dumps(sections)), turn()])
    request = replace(page_request(), current_body="## A\n\nOld.", changes=["added module module:app/b.py"])
    draft = await adapter(source, program).write_page(request)
    assert draft.sections == [{"heading": "## A", "body": "New."}]


@pytest.mark.anyio
async def test_a_digest_returns_its_highlights(source: Path, tmp_path: Path) -> None:
    from codetrail.assistant import DigestRequest

    digest = {"title": "T", "body": "B", "highlights": ["One.", "Two."]}
    program = make_program(tmp_path / "bin", "fake-codex", [message(json.dumps(digest)), turn()])
    draft = await adapter(source, program).write_digest(DigestRequest("shop", "", "", max_highlights=2))
    assert draft.highlights == ["One.", "Two."]


@pytest.mark.anyio
async def test_overview_refuses_codex_adapter(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", [])
    with pytest.raises(AssistantError, match="overview can't use Codex"):
        await adapter(source, program).write_overview(OverviewRequest("platform", [], 250))
    assert not program.record_file.exists()  # Codex never ran


@pytest.mark.anyio
async def test_grading_refuses_codex(source: Path, tmp_path: Path) -> None:
    """Grading has no tools at all (design 8.2), and Codex can't run without its shell (15.5)."""
    program = make_program(tmp_path / "bin", "fake-codex", [])
    with pytest.raises(AssistantError, match="Grading can't use Codex"):
        await adapter(source, program).grade(GradeRequest("Why?", [], "The app", "body", "answer", "en"))
    assert not program.record_file.exists()  # Codex never ran


@pytest.mark.anyio
@pytest.mark.parametrize("lines", [[], [json.dumps(turn())]], ids=["no turn ended", "one turn ended"])
async def test_a_call_that_passes_its_time_limit_counts_its_token_cap_and_isnt_retried(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: list[str]
) -> None:
    """Codex reports tokens only when a turn ends, so a call stopped mid-turn counts its whole token limit."""
    starts: list[list[str]] = []

    async def stopped_at_the_limit(command: list[str], *args: object, **options: object) -> AsyncIterator[str]:
        starts.append(command)
        for line in lines:
            yield line
        await anyio.sleep(1)  # the runner's limit passes; a real program is killed here
        raise AssistantError("The provider's program passed its time limit and was stopped.")

    monkeypatch.setattr(runner, "run_program", stopped_at_the_limit)
    program = make_program(tmp_path / "bin", "fake-codex", [])
    codex = CodexAssistant(source, {"write": "gpt-5.5-codex"},
                           CodexSettings(command=str(program.path), max_tokens_per_call=4000, timeout_seconds=1),
                           GenerationSettings(), retries=2, environ={"PATH": "/usr/bin:/bin"})  # fmt: skip
    with pytest.raises(PaidCallFailed, match="time limit") as raised:
        await codex.write_page(page_request())
    usage = raised.value.usage
    assert (usage.provider, usage.model, usage.tokens) == ("codex", "gpt-5.5-codex", 4000)
    assert usage.cached_input_tokens == (400 if lines else 0)  # what Codex reported keeps its kind
    assert len(starts) == 1  # no retry


@pytest.mark.anyio
async def test_a_configured_output_cap_stops_codex(source: Path, tmp_path: Path) -> None:
    program = make_program(tmp_path / "bin", "fake-codex", [{"type": "thread.started", "id": "x" * 500},
                                                            message(json.dumps(PAGE)), turn()])  # fmt: skip
    codex = adapter(source, program)
    codex.max_output_bytes = 200
    with pytest.raises(AssistantError, match="more than 200 bytes"):
        await codex.write_page(page_request())
