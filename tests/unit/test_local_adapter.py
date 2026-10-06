"""The local adapter: a model on the reader's machine, through Codetrail's own read-only tools (design 15.1, 15.5)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from codetrail.assistant import AssistantError, GradeRequest, PageRequest, QuestionRequest
from codetrail.assistant.local import LocalAssistant
from codetrail.assistant.prompts import FULLWIDTH_AT
from codetrail.config import GenerationSettings, LocalSettings

PAGE = {"body": "A page.", "checks": []}


def reply(
    content: str | None = None,
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    tokens: tuple[int, int] = (100, 20),
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": f"call_{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for index, (name, args) in enumerate(calls)
        ]
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}}


class Endpoint:
    """A scripted OpenAI-compatible endpoint that records each request."""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json=self.replies.pop(0))


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text("print('hi')\n")
    return root


def adapter(source: Path, handler: Callable[[httpx.Request], httpx.Response], **settings: Any) -> LocalAssistant:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:11434/v1")
    models = {"write": "qwen3:14b", "answer": "qwen3:14b", "grade": "qwen3:14b"}
    return LocalAssistant(source, models, LocalSettings(**settings), GenerationSettings(), client=client)


def page_request() -> PageRequest:
    return PageRequest("areas/app", "area", "The app", ["app"], "see @app/main.py", "", "")


@pytest.mark.anyio
async def test_a_page_reads_through_the_tools_then_answers_json(source: Path) -> None:
    endpoint = Endpoint(
        [
            reply(calls=[("read", {"path": "app/main.py"}), ("read", {"path": "/etc/hosts"})]),
            reply("```json\n" + json.dumps(PAGE) + "\n```"),
        ]
    )
    draft = await adapter(source, endpoint).write_page(page_request())
    assert (draft.body, draft.files_read) == ("A page.", ["app/main.py"])
    first, second = endpoint.requests
    assert first["model"] == "qwen3:14b"
    assert {tool["function"]["name"] for tool in first["tools"]} == {"read", "grep", "glob"}
    assert f"{FULLWIDTH_AT}app/main.py" in first["messages"][-1]["content"]
    results = [message["content"] for message in second["messages"] if message["role"] == "tool"]
    assert results[0] == "1\tprint('hi')" and results[1].startswith("Refused:")
    usage = draft.usage
    assert (usage.provider, usage.input_tokens, usage.output_tokens, usage.cost_usd) == ("local", 200, 40, 0.0)


@pytest.mark.anyio
async def test_a_bad_answer_gets_one_retry_with_the_errors(source: Path) -> None:
    endpoint = Endpoint([reply("not json"), reply(json.dumps(PAGE))])
    assert (await adapter(source, endpoint).write_page(page_request())).body == "A page."
    assert "JSON" in endpoint.requests[1]["messages"][-1]["content"]


@pytest.mark.anyio
async def test_a_second_bad_answer_fails(source: Path) -> None:
    endpoint = Endpoint([reply("not json"), reply(json.dumps({"body": "missing checks"}))])
    with pytest.raises(AssistantError, match="checks"):
        await adapter(source, endpoint).write_page(page_request())


@pytest.mark.anyio
async def test_the_turn_and_token_limits_stop_the_loop(source: Path) -> None:
    looping = Endpoint([reply(calls=[("glob", {"pattern": "*"})]) for _ in range(5)])
    with pytest.raises(AssistantError, match="turn"):
        await adapter(source, looping, max_turns=3).write_page(page_request())
    greedy = Endpoint([reply(calls=[("glob", {"pattern": "*"})], tokens=(900, 200)), reply(json.dumps(PAGE))])
    with pytest.raises(AssistantError, match="token limit"):
        await adapter(source, greedy, max_tokens_per_call=1000).write_page(page_request())


@pytest.mark.anyio
async def test_grading_offers_no_tools(source: Path) -> None:
    endpoint = Endpoint([reply(json.dumps({"verdict": "pass", "missed": [], "feedback": "Good."}))])
    verdict = await adapter(source, endpoint).grade(GradeRequest("Why?", [], "Page", "Body", "Because.", "en"))
    assert verdict.verdict == "pass" and "tools" not in endpoint.requests[0]


@pytest.mark.anyio
async def test_an_answer_is_buffered_then_finished(source: Path) -> None:
    endpoint = Endpoint([reply("Hello there.")])
    chunks = [chunk async for chunk in adapter(source, endpoint).answer(QuestionRequest("shop", "What?", "en"))]
    assert [chunk.text for chunk in chunks] == ["Hello there."] and chunks[-1].done


@pytest.mark.anyio
async def test_an_endpoint_error_is_reported(source: Path) -> None:
    with pytest.raises(AssistantError, match="500"):
        await adapter(source, lambda request: httpx.Response(500, text="boom")).write_page(page_request())


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",
        "http://127.0.0.1@evil.example/v1",
        "http://localhost.evil.example/v1",
        "http://user:pass@127.0.0.1:11434/v1",
        "file:///etc/hosts",
        "http://10.0.0.5:11434/v1",
    ],
)
def test_only_loopback_endpoints_are_accepted(url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        LocalSettings(base_url=url)


def test_loopback_endpoints_are_accepted() -> None:
    for url in ("http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://[::1]:11434/v1"):
        assert LocalSettings(base_url=url).base_url == url


def test_the_real_client_ignores_proxies_and_redirects(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    assistant = LocalAssistant(source, {}, LocalSettings(), GenerationSettings())
    assert assistant.client.follow_redirects is False
    assert assistant.client._trust_env is False


@pytest.mark.anyio
async def test_a_long_unclosed_fence_is_parsed_quickly(source: Path) -> None:
    import time

    endpoint = Endpoint([reply("```" + " " * 200_000), reply(json.dumps(PAGE))])
    started = time.monotonic()
    assert (await adapter(source, endpoint).write_page(page_request())).body == "A page."
    assert time.monotonic() - started < 1


@pytest.mark.anyio
async def test_tool_calls_per_turn_are_capped(source: Path) -> None:
    many = [("read", {"path": "app/main.py"})] * 50
    endpoint = Endpoint([reply(calls=many), reply(json.dumps(PAGE))])
    await adapter(source, endpoint).write_page(page_request())
    results = [message["content"] for message in endpoint.requests[1]["messages"] if message["role"] == "tool"]
    assert len(results) == 50
    assert sum(result.startswith("Refused: too many") for result in results) == 30


@pytest.mark.anyio
async def test_a_revision_returns_its_sections(source: Path) -> None:
    from dataclasses import replace

    sections = {"sections": [{"heading": "## A", "body": "New."}], "checks": []}
    endpoint = Endpoint([reply(json.dumps(sections))])
    request = replace(page_request(), current_body="## A\n\nOld.", changes=["added module module:app/b.py"])
    assert (await adapter(source, endpoint).write_page(request)).sections == [{"heading": "## A", "body": "New."}]
