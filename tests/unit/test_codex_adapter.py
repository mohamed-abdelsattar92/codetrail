"""The codex adapter: the reader's own `codex exec`, read-only, isolated from their config (design 15.1, 15.5)."""

import json
from pathlib import Path
from typing import Any

import pytest

from codetrail.assistant import AssistantError, PageRequest, QuestionRequest
from codetrail.assistant.codex import CodexAssistant
from codetrail.assistant.prompts import FULLWIDTH_AT
from codetrail.config import CodexSettings, GenerationSettings
from tests.fixtures.programs import FakeProgram, make_program

PAGE = {"body": "A page.", "checks": []}


def message(text: str) -> dict[str, Any]:
    return {"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": text}}


def turn(input_tokens: int = 1000, cached: int = 400, output: int = 80) -> dict[str, Any]:
    usage = {"input_tokens": input_tokens, "cached_input_tokens": cached, "output_tokens": output}
    return {"type": "turn.completed", "usage": usage}


def command_item(command: str) -> dict[str, Any]:
    return {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": command,
                                               "exit_code": 0, "status": "completed"}}  # fmt: skip


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print('hi')\n")
    return root


def adapter(source: Path, program: FakeProgram, auth: str = "subscription", max_tokens: int = 400_000,
            model: str = "gpt-5.5-codex") -> CodexAssistant:  # fmt: skip
    return CodexAssistant(
        source,
        {"write": model, "answer": model},
        CodexSettings(command=str(program.path), auth=auth, max_tokens_per_call=max_tokens),  # type: ignore[arg-type]
        GenerationSettings(),
        retries=0,
        environ={"PATH": "/usr/bin:/bin", "HOME": "/Users/reader", "USER": "reader", "LOGNAME": "reader",
                 "SHELL": "/bin/zsh", "OPENAI_API_KEY": "key-2", "GITHUB_TOKEN": "token-1"},
    )  # fmt: skip


def page_request() -> PageRequest:
    return PageRequest("areas/app", "area", "The app", ["app"], "see @app/main.py", "", "")


@pytest.mark.anyio
async def test_a_page_runs_codex_read_only_and_isolated(source: Path, tmp_path: Path) -> None:
    events = [{"type": "thread.started"}, command_item("bash -lc 'cat app/main.py'"), message(json.dumps(PAGE)),
              turn()]  # fmt: skip
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
    assert overrides == ["mcp_servers={}", "notify=[]", "project_doc_max_bytes=0",
                         'shell_environment_policy.inherit="none"']  # fmt: skip
    assert record["schema"]["required"] == ["body", "checks"]
    assert f"{FULLWIDTH_AT}app/main.py" in record["stdin"]
    assert record["cwd"] == str(source.resolve())
    assert (draft.body, draft.files_read) == ("A page.", ["app/main.py"])
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
async def test_an_answer_is_buffered_until_codex_finishes(source: Path, tmp_path: Path) -> None:
    events = [message("Hello there."), turn()]
    program = make_program(tmp_path / "bin", "fake-codex", events)
    chunks = [chunk async for chunk in adapter(source, program).answer(QuestionRequest("shop", "What?", "en"))]
    assert [chunk.text for chunk in chunks] == ["Hello there.", ""]
    assert chunks[-1].done and chunks[-1].usage.output_tokens == 80
    assert "--output-schema" not in program.record["argv"]
