"""The Claude Agent SDK adapter: the only code that imports the SDK (design section 6.9).

Each call runs Claude with the working directory `source/`, only Read, Grep and Glob, the tool guard as a PreToolUse
hook (which sees every call, read-only ones included), no settings, hooks, skills, MCP servers or CLAUDE.md from
anywhere, Codetrail's own system prompt, and a limit on turns and cost. Answers come back as structured output.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import anyio
from claude_agent_sdk import (
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    ResultMessage,
    StreamEvent,
    query,
)

from codetrail.claude import (
    AnswerChunk,
    ClaudeError,
    DigestDraft,
    DigestRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
    QuestionRequest,
)
from codetrail.claude.guard import ALLOWED_TOOLS, ToolGuard
from codetrail.claude.prompts import (
    ANSWER_RULES,
    DIGEST_SCHEMA,
    GROUND_RULES,
    PAGE_SCHEMA,
    PAGE_SYNTAX,
    PLAN_SCHEMA,
    answer_prompt,
    digest_prompt,
    page_prompt,
    plan_prompt,
)
from codetrail.config import GenerationSettings, ModelSettings

CALL_TIMEOUT_SECONDS = 900
# Claude Code attaches the file an @path in a prompt names, before any tool call and past the guard (a probe proved
# it). Prompts carry repository text, so every @ becomes a fullwidth at sign the model reads but Claude Code ignores.
FULLWIDTH_AT = "\uff20"


def neutralize(prompt: str) -> str:
    return prompt.replace("@", FULLWIDTH_AT)


def sdk_hook(guard: ToolGuard) -> Any:
    """The guard as the SDK's PreToolUse hook."""

    async def hook(input_data: HookInput, tool_use_id: str | None, context: HookContext) -> HookJSONOutput:
        return cast(HookJSONOutput, await guard.hook(cast(dict[str, Any], input_data), tool_use_id, context))

    return hook


class AgentSdkClaude:
    def __init__(
        self,
        source_root: Path,
        models: ModelSettings,
        limits: GenerationSettings,
        retries: int = 2,
        answer_limits: tuple[int, float] = (20, 1.0),
    ) -> None:
        self.root = source_root
        self.models = models
        self.limits = limits
        self.retries = retries
        self.answer_limits = answer_limits  # max turns and budget of one bridge answer

    def options(
        self, guard: ToolGuard, model: str, schema: dict[str, Any] | None, system_prompt: str
    ) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            tools=list(ALLOWED_TOOLS),
            allowed_tools=[],
            setting_sources=[],
            mcp_servers={},
            strict_mcp_config=True,
            system_prompt=system_prompt,
            model=model,
            max_turns=self.limits.max_turns,
            max_budget_usd=self.limits.max_budget_usd_per_call,
            cwd=str(self.root),
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[sdk_hook(guard)])]},
            output_format={"type": "json_schema", "schema": schema} if schema is not None else None,
        )

    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        """Streams the answer's text as Claude writes it, then a final chunk with the files read and the cost."""
        guard = ToolGuard(self.root)
        options = replace(
            self.options(guard, self.models.answer, None, GROUND_RULES + "\n" + PAGE_SYNTAX + "\n" + ANSWER_RULES),
            include_partial_messages=True,
            max_turns=self.answer_limits[0],
            max_budget_usd=self.answer_limits[1],
        )
        result: ResultMessage | None = None
        with anyio.fail_after(CALL_TIMEOUT_SECONDS):
            async for message in query(prompt=answer_prompt(request), options=options):
                if isinstance(message, StreamEvent):
                    event = message.event
                    delta = event.get("delta") or {}
                    if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        yield AnswerChunk(text=str(delta.get("text", "")))
                elif isinstance(message, ResultMessage):
                    result = message
        if result is None or result.is_error:
            raise ClaudeError(f"Claude didn't finish the answer ({result.subtype if result else 'no result'}).")
        yield AnswerChunk(done=True, files_read=guard.files_read, cost_usd=float(result.total_cost_usd or 0.0))

    async def plan(self, request: PlanRequest) -> PlanDraft:
        data, files_read, cost = await self._run(plan_prompt(request), self.models.plan, PLAN_SCHEMA, GROUND_RULES)
        return PlanDraft(list(data.get("pages", [])), files_read, cost)

    async def write_page(self, request: PageRequest) -> PageDraft:
        data, files_read, cost = await self._run(
            page_prompt(request), self.models.write, PAGE_SCHEMA, GROUND_RULES + "\n" + PAGE_SYNTAX
        )
        return PageDraft(str(data.get("body", "")), list(data.get("checks", [])), files_read, cost)

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        data, files_read, cost = await self._run(
            digest_prompt(request), self.models.digest, DIGEST_SCHEMA, GROUND_RULES + "\n" + PAGE_SYNTAX
        )
        return DigestDraft(str(data.get("title", "")), str(data.get("body", "")), files_read, cost)

    async def _run(
        self, prompt: str, model: str, schema: dict[str, Any], system_prompt: str
    ) -> tuple[dict[str, Any], list[str], float]:
        last_error: Exception | None = None
        for _attempt in range(self.retries + 1):
            guard = ToolGuard(self.root)
            try:
                result = await self._query(neutralize(prompt), self.options(guard, model, schema, system_prompt))
            except ClaudeError:
                raise
            except Exception as error:  # the CLI process or the connection failed: worth another try
                last_error = error
                await anyio.sleep(2)
                continue
            if result.is_error or not isinstance(result.structured_output, dict):
                raise ClaudeError(f"Claude didn't finish the task ({result.subtype}).")
            return result.structured_output, guard.files_read, float(result.total_cost_usd or 0.0)
        raise ClaudeError(f"Claude couldn't be reached ({type(last_error).__name__}).")

    @staticmethod
    async def _query(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
        result: ResultMessage | None = None
        with anyio.fail_after(CALL_TIMEOUT_SECONDS):
            async for message in query(prompt=prompt, options=options):  # read to the end, so the SDK can clean up
                if isinstance(message, ResultMessage):
                    result = message
        if result is None:
            raise ClaudeError("Claude ended without a result.")
        return result
