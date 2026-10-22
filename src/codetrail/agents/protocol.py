"""The tool server's protocol: the Model Context Protocol over standard input and output (ADR 0017).

JSON-RPC 2.0, one message per line, with only what Codetrail needs: `initialize`, `ping`, `tools/list` and
`tools/call`; notifications get no answer. It listens on no port. Standard output carries protocol messages and
nothing else; anything unexpected is logged to standard error, never sent to the agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any, BinaryIO

from codetrail import __version__
from codetrail.agents.tools import TOOLS, AgentTools, ToolInputError

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # newest first
MAX_LINE_BYTES = 1_048_576
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
TOOL_NAMES = {str(tool["name"]) for tool in TOOLS}

logger = logging.getLogger(__name__)


def handle(message: object, tools: AgentTools, instructions: str) -> dict[str, object] | None:
    """The answer to one message, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        identity = message.get("id") if isinstance(message, dict) else None
        return _error(identity, INVALID_REQUEST, "Not a JSON-RPC 2.0 request.")
    if "id" not in message:
        return None
    identity, method, params = message["id"], message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return _error(identity, INVALID_PARAMS, "params must be an object.")
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(identity, {"protocolVersion": version, "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "codetrail", "version": __version__},
                                  "instructions": instructions})  # fmt: skip
    if method == "ping":
        return _result(identity, {})
    if method == "tools/list":
        return _result(identity, {"tools": tools.definitions()})
    if method == "tools/call":
        return _call(identity, params, tools)
    return _error(identity, METHOD_NOT_FOUND, f"There is no method {method!r}.")


def serve_stdio(tools: AgentTools, instructions: str, reader: BinaryIO, writer: BinaryIO) -> None:
    """Answers one message per line until the input ends."""
    while True:
        line = reader.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_LINE_BYTES:  # the rest of the line is read and dropped
            while line and not line.endswith(b"\n"):
                line = reader.readline(MAX_LINE_BYTES)
            _send(writer, _error(None, PARSE_ERROR, f"A message is at most {MAX_LINE_BYTES} bytes."))
            continue
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError, RecursionError:  # RecursionError: deeply nested arrays
            _send(writer, _error(None, PARSE_ERROR, "The message isn't valid JSON."))
            continue
        if isinstance(message, list):
            _send(writer, _error(None, INVALID_REQUEST, "Batches aren't supported."))
            continue
        try:
            answer = handle(message, tools, instructions)
        except Exception:  # the server outlives any one message; the details go to standard error only
            logger.exception("A message couldn't be handled")
            identity = message.get("id") if isinstance(message, dict) else None
            answer = _error(
                identity, INTERNAL_ERROR, "The message couldn't be handled; see the server's standard error."
            )
        if answer is not None:
            _send(writer, answer)


def _call(identity: object, params: dict[str, Any], tools: AgentTools) -> dict[str, object]:
    name, arguments = params.get("name"), params.get("arguments")
    if arguments is None:  # some clients send null, or nothing, for a tool without arguments
        arguments = {}
    if not isinstance(name, str) or name not in TOOL_NAMES:
        return _error(identity, INVALID_PARAMS, f"There is no tool {name!r}.")
    if not isinstance(arguments, dict):
        return _error(identity, INVALID_PARAMS, "arguments must be an object.")
    try:
        text, failed = tools.call(str(name), arguments), False
    except ToolInputError as error:
        text, failed = str(error), True
    except Exception:  # never a traceback to the agent: it goes to standard error only
        logger.exception("The tool %s failed", name)
        return _error(identity, INTERNAL_ERROR, "The tool failed; see the server's standard error.")
    return _result(identity, {"content": [{"type": "text", "text": text}], "isError": failed})


def _result(identity: object, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identity, "result": result}


def _error(identity: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": identity, "error": {"code": code, "message": message}}


def _send(writer: BinaryIO, message: dict[str, object]) -> None:
    writer.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")
    writer.flush()
