"""The Claude Agent SDK adapter: the only code that imports the SDK (design section 6.9).

Each call runs Claude with the working directory `source/`, only Read, Grep and Glob, the tool guard as a PreToolUse
hook (which sees every call, read-only ones included), no settings, hooks, skills, MCP servers or CLAUDE.md from
anywhere, Codetrail's own system prompt, and a limit on turns and cost. Answers come back as structured output.
"""

from __future__ import annotations

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
    query,
)

from codetrail.claude import (
    ClaudeError,
    DigestDraft,
    DigestRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
)
from codetrail.claude.guard import ALLOWED_TOOLS, ToolGuard
from codetrail.claude.prompts import (
    DIGEST_SCHEMA,
    GROUND_RULES,
    PAGE_SCHEMA,
    PAGE_SYNTAX,
    PLAN_SCHEMA,
    digest_prompt,
    page_prompt,
    plan_prompt,
)
from codetrail.config import GenerationSettings, ModelSettings

CALL_TIMEOUT_SECONDS = 900


def sdk_hook(guard: ToolGuard) -> Any:
    """The guard as the SDK's PreToolUse hook."""

    async def hook(input_data: HookInput, tool_use_id: str | None, context: HookContext) -> HookJSONOutput:
        return cast(HookJSONOutput, await guard.hook(cast(dict[str, Any], input_data), tool_use_id, context))

    return hook


class AgentSdkClaude:
    def __init__(self, source_root: Path, models: ModelSettings, limits: GenerationSettings, retries: int = 2) -> None:
        self.root = source_root
        self.models = models
        self.limits = limits
        self.retries = retries

    def options(self, guard: ToolGuard, model: str, schema: dict[str, Any], system_prompt: str) -> ClaudeAgentOptions:
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
            output_format={"type": "json_schema", "schema": schema},
        )

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
                result = await self._query(prompt, self.options(guard, model, schema, system_prompt))
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
        with anyio.fail_after(CALL_TIMEOUT_SECONDS):
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    return message
        raise ClaudeError("Claude ended without a result.")
