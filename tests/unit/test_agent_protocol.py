"""The tool server's protocol: JSON-RPC 2.0, one message per line, over standard input and output (ADR 0017)."""

import io
import json
from pathlib import Path
from typing import Any

import pytest

from codetrail.agents.protocol import MAX_LINE_BYTES, PROTOCOL_VERSIONS, serve_stdio
from codetrail.agents.tools import AgentTools
from codetrail.config import Paths
from tests.unit.test_agent_tools import paths as paths  # the fixture
from tests.unit.test_agent_tools import tools_for


@pytest.fixture
def tools(paths: Paths) -> AgentTools:
    return tools_for(paths)


def line(message: object) -> bytes:
    return json.dumps(message).encode() + b"\n"


def request(number: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    return line({"jsonrpc": "2.0", "id": number, "method": method, **({"params": params} if params else {})})


def run(tools: AgentTools, *lines: bytes) -> list[dict[str, Any]]:
    output = io.BytesIO()
    serve_stdio(tools, "Instructions for t at /work/t", io.BytesIO(b"".join(lines)), output)
    return [json.loads(text) for text in output.getvalue().splitlines()]


def test_initialize_negotiates_the_version_and_gives_instructions(tools: AgentTools) -> None:
    known, unknown = run(
        tools,
        request(1, "initialize", {"protocolVersion": "2025-06-18"}),
        request(2, "initialize", {"protocolVersion": "1999-01-01"}),
    )
    assert known["result"]["protocolVersion"] == "2025-06-18"
    assert unknown["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    result = known["result"]
    assert result["capabilities"] == {"tools": {}} and result["serverInfo"]["name"] == "codetrail"
    assert result["instructions"] == "Instructions for t at /work/t"


def test_tools_list_and_call(tools: AgentTools) -> None:
    listed, called, ping = run(
        tools,
        request(1, "tools/list"),
        request(2, "tools/call", {"name": "codetrail_map", "arguments": {}}),
        request(3, "ping"),
    )
    assert listed["result"]["tools"][0]["name"] == "codetrail_map"
    assert called["id"] == 2 and called["result"]["isError"] is False
    assert called["result"]["content"][0]["text"].startswith("# Map of t")
    assert ping == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_a_bad_argument_is_a_tool_error_not_a_crash(tools: AgentTools) -> None:
    [answer] = run(tools, request(1, "tools/call", {"name": "codetrail_page", "arguments": {"page": "../x"}}))
    assert answer["result"]["isError"] is True and "page id" in answer["result"]["content"][0]["text"]


def test_notifications_get_no_response(tools: AgentTools) -> None:
    assert run(tools, line({"jsonrpc": "2.0", "method": "notifications/initialized"})) == []


def test_errors_have_their_codes(tools: AgentTools) -> None:
    answers = run(
        tools,
        b"not json\n",
        request(1, "resources/list"),
        request(2, "tools/call", {"name": "codetrail_write", "arguments": {}}),
        request(3, "tools/call", {"name": "codetrail_map", "arguments": []}),
        request(6, "tools/call", {"name": ["codetrail_map"]}),
        b"[" * 200_000 + b"\n",
        b"x" * (2 * MAX_LINE_BYTES) + b"\n",
        line({"id": 4, "method": "ping"}),
        request(5, "ping"),
    )
    assert [answer.get("error", {}).get("code") for answer in answers] == [
        -32700, -32601, -32602, -32602, -32602, -32700, -32700, -32600, None
    ]  # fmt: skip
    assert answers[0]["id"] is None and answers[-1]["id"] == 5  # the line after the long one is still answered


def test_batches_are_refused(tools: AgentTools) -> None:
    [answer] = run(tools, line([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]))
    assert answer["error"]["code"] == -32600 and answer["id"] is None


def test_end_of_input_ends_the_server(tools: AgentTools) -> None:
    assert run(tools) == []


def test_an_unexpected_failure_is_an_internal_error(tools: AgentTools, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(tool: str, arguments: object) -> str:
        raise RuntimeError("/secret/path in a traceback")

    monkeypatch.setattr(tools, "call", broken)
    [answer] = run(tools, request(1, "tools/call", {"name": "codetrail_map", "arguments": {}}))
    assert answer["error"]["code"] == -32603 and "/secret/path" not in json.dumps(answer)


def test_requests_before_initialize_are_answered(tools: AgentTools, tmp_path: Path) -> None:
    [answer] = run(tools, request(1, "tools/call", {"name": "codetrail_map", "arguments": {}}))
    assert answer["result"]["isError"] is False


def test_the_tool_list_names_the_target(tools: AgentTools) -> None:
    [listed] = run(tools, request(1, "tools/list"))
    assert all("/work/t" in tool["description"] for tool in listed["result"]["tools"])


def test_null_arguments_are_no_arguments(tools: AgentTools) -> None:
    [answer] = run(tools, request(1, "tools/call", {"name": "codetrail_map", "arguments": None}))
    assert answer["result"]["isError"] is False


def test_any_unexpected_failure_still_answers_and_the_server_lives_on(
    tools: AgentTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codetrail.agents import protocol

    original = protocol.handle
    calls: list[int] = []

    def first_breaks(message: object, tools: AgentTools, instructions: str) -> dict[str, object] | None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("/secret/path")
        return original(message, tools, instructions)

    monkeypatch.setattr(protocol, "handle", first_breaks)
    failed, ping = run(tools, request(7, "ping"), request(8, "ping"))
    assert failed["id"] == 7 and failed["error"]["code"] == -32603 and "/secret/path" not in json.dumps(failed)
    assert ping["result"] == {}
