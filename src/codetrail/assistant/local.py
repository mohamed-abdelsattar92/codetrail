"""The local adapter: a model on the reader's machine, through Codetrail's own read-only tools (design 15.1, 15.5).

It talks to an OpenAI-compatible chat endpoint on loopback (Ollama or LM Studio) with httpx, ignoring proxy settings
and never following redirects. The model may call Read, Grep and Glob, which Codetrail runs over `source/` through the
tool guard. A structured answer must be JSON with the schema's keys; one retry is given with the errors. Turns,
tokens and time are limited by configuration. Answers are buffered, so the bridge can scan them before the reader sees
them. Local models cost nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import anyio
import httpx

from codetrail.assistant import (
    AnswerChunk,
    AssistantError,
    DigestDraft,
    DigestRequest,
    GradeRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
    QuestionRequest,
    Usage,
    Verdict,
)
from codetrail.assistant.local_tools import SCHEMAS, LocalTools
from codetrail.assistant.prompts import (
    ANSWER_RULES,
    DIGEST_SCHEMA,
    GRADE_RULES,
    GRADE_SCHEMA,
    GROUND_RULES,
    PAGE_SCHEMA,
    PAGE_SYNTAX,
    PLAN_SCHEMA,
    answer_prompt,
    digest_prompt,
    grade_prompt,
    neutralize,
    page_prompt,
    plan_prompt,
)
from codetrail.config import GenerationSettings, LocalSettings

PROVIDER: Final = "local"
FENCED_JSON = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
TOOL_RULES = "Use the read, grep and glob tools to look at the repository's files before you answer."


@dataclass
class _Result:
    text: str
    files_read: list[str]
    usage: Usage


class LocalAssistant:
    def __init__(
        self,
        source_root: Path,
        models: Mapping[str, str],
        settings: LocalSettings,
        limits: GenerationSettings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.root = source_root.resolve()
        self.models = dict(models)
        self.settings = settings
        self.limits = limits
        self.client = client or httpx.AsyncClient(
            base_url=settings.base_url,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(settings.timeout_seconds),
        )

    async def plan(self, request: PlanRequest) -> PlanDraft:
        data, result = await self._structured("plan", GROUND_RULES, plan_prompt(request), PLAN_SCHEMA)
        return PlanDraft(list(data.get("pages", [])), list(data.get("paths", [])), result.files_read, 0.0, result.usage)

    async def write_page(self, request: PageRequest) -> PageDraft:
        data, result = await self._structured(
            "write", GROUND_RULES + "\n" + PAGE_SYNTAX, page_prompt(request), PAGE_SCHEMA
        )
        return PageDraft(str(data.get("body", "")), list(data.get("checks", [])), result.files_read, 0.0, result.usage)

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        data, result = await self._structured(
            "digest", GROUND_RULES + "\n" + PAGE_SYNTAX, digest_prompt(request), DIGEST_SCHEMA
        )
        return DigestDraft(str(data.get("title", "")), str(data.get("body", "")), result.files_read, 0.0, result.usage)

    async def grade(self, request: GradeRequest) -> Verdict:
        data, result = await self._structured("grade", GRADE_RULES, grade_prompt(request), GRADE_SCHEMA, tools=False)
        missed = [str(item) for item in data.get("missed", [])]
        return Verdict(str(data.get("verdict", "")), missed, str(data.get("feedback", "")), 0.0, result.usage)

    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        rules = GROUND_RULES + "\n" + PAGE_SYNTAX + "\n" + ANSWER_RULES
        result = await self._loop("answer", rules, answer_prompt(request), None)
        yield AnswerChunk(text=result.text)
        yield AnswerChunk(done=True, files_read=result.files_read, usage=result.usage)

    async def _structured(
        self, kind: str, rules: str, prompt: str, schema: dict[str, Any], tools: bool = True
    ) -> tuple[dict[str, Any], _Result]:
        result = await self._loop(kind, rules, prompt, schema, tools)
        data = _parse(result.text, schema)
        if isinstance(data, str):
            raise AssistantError(f"The local model's answer wasn't usable: {data}")
        return data, result

    async def _loop(
        self, kind: str, rules: str, prompt: str, schema: dict[str, Any] | None, tools: bool = True
    ) -> _Result:
        model = self.models.get(kind, "")
        files = LocalTools(self.root, self.settings.max_read_bytes)
        system = rules
        if tools:
            system += "\n" + TOOL_RULES
        if schema is not None:
            system += "\nFinish with only a JSON object that follows this schema, and nothing else:\n" + json.dumps(
                schema
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": neutralize(prompt)},
        ]
        tokens_in = tokens_out = 0
        retried = False
        with anyio.fail_after(self.settings.timeout_seconds):
            for _turn in range(self.settings.max_turns):
                body: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
                if tools:
                    body["tools"] = SCHEMAS
                reply = await self._chat(body)
                usage = reply.get("usage") or {}
                tokens_in += _count(usage, "prompt_tokens")
                tokens_out += _count(usage, "completion_tokens")
                if tokens_in + tokens_out > self.settings.max_tokens_per_call:
                    raise AssistantError(
                        f"The local model passed its token limit ({self.settings.max_tokens_per_call})."
                    )
                message = _message(reply)
                calls = message.get("tool_calls") or []
                if calls and tools:
                    messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
                    for call in calls:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": str(call.get("id", "")),
                                "content": await _run_tool(files, call),
                            }
                        )
                    continue
                text = str(message.get("content") or "")
                if schema is not None and isinstance(problem := _parse(text, schema), str) and not retried:
                    retried = True
                    messages += [
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": f"That wasn't usable: {problem} Answer again with only the JSON object.",
                        },
                    ]
                    continue
                usage_record = Usage(PROVIDER, model, tokens_in, 0, tokens_out, 0.0)
                return _Result(text, list(files.files_read), usage_record)
        raise AssistantError(f"The local model didn't finish within {self.settings.max_turns} turns.")

    async def _chat(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.post("chat/completions", json=body)
        except httpx.HTTPError as error:
            raise AssistantError(
                f"The local model at {self.settings.base_url} couldn't be reached ({type(error).__name__})."
            ) from error
        if response.status_code != 200:
            raise AssistantError(f"The local model answered with HTTP {response.status_code}.")
        try:
            reply = response.json()
        except ValueError as error:
            raise AssistantError("The local model's reply wasn't JSON.") from error
        if not isinstance(reply, dict):
            raise AssistantError("The local model's reply wasn't a JSON object.")
        return reply


async def _run_tool(files: LocalTools, call: Mapping[str, Any]) -> str:
    function = call.get("function") or {}
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except ValueError:
        return "Refused: the tool's arguments weren't JSON."
    if not isinstance(arguments, dict):
        return "Refused: the tool's arguments weren't a JSON object."
    # In a worker thread, so a slow search can't block the event loop or outlast the time limit.
    return await anyio.to_thread.run_sync(files.run, str(function.get("name", "")), arguments, abandon_on_cancel=True)


def _message(reply: Mapping[str, Any]) -> dict[str, Any]:
    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AssistantError("The local model's reply had no message.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AssistantError("The local model's reply had no message.")
    return message


def _parse(text: str, schema: Mapping[str, Any]) -> dict[str, Any] | str:
    """The answer as a JSON object with the schema's required keys, or what is wrong with it."""
    fenced = FENCED_JSON.match(text)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except ValueError:
        return "it isn't valid JSON."
    if not isinstance(data, dict):
        return "it isn't a JSON object."
    missing = [key for key in schema.get("required", []) if key not in data]
    if missing:
        return f"it lacks {', '.join(missing)}."
    return data


def _count(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0
