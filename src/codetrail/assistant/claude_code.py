"""The claude_code adapter: the reader's own `claude -p`, read-only, on the reader's sign-in (design 15.1, 15.2).

Each call runs `claude -p` in `source/` with the prompt on stdin and two independent read-only layers: Claude Code's
own permissions (only Read, Grep and Glob, allowed only under the working folder, `dontAsk` refusing the rest) and
Codetrail's tool guard as a PreToolUse hook. No settings, MCP servers or slash commands of the reader's or the
target's load. The environment comes from an allowlist, so with a subscription no key is visible. Structured answers
come back through the `StructuredOutput` tool; usage, cost and the plan's usage windows come from the stream.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import aclosing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import anyio

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
    PlanWindow,
    QuestionRequest,
    Usage,
    Verdict,
    runner,
)
from codetrail.assistant.environment import child_environment, resolve_program
from codetrail.assistant.guard import ALLOWED_TOOLS
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
from codetrail.config import ClaudeCodeSettings, GenerationSettings

PROVIDER: Final = "claude_code"


class _ProgramFailed(AssistantError):
    """The program ended without a result: worth another try."""


@dataclass
class _Outcome:
    result: dict[str, Any] | None = None
    windows: dict[str, PlanWindow] = field(default_factory=dict)


class ClaudeCodeAssistant:
    def __init__(
        self,
        source_root: Path,
        models: Mapping[str, str],
        settings: ClaudeCodeSettings,
        limits: GenerationSettings,
        retries: int = 2,
        answer_limits: tuple[int, float] = (20, 1.0),
        grade_budget_usd: float = 0.25,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self.root = source_root.resolve()
        self.models = dict(models)
        self.settings = settings
        self.limits = limits
        self.retries = retries
        self.answer_limits = answer_limits  # max turns and budget of one bridge answer
        self.grade_budget_usd = grade_budget_usd
        self.environment = child_environment(PROVIDER, settings.auth, environ)
        self.program = resolve_program(settings.command, self.environment.get("PATH", ""))

    async def plan(self, request: PlanRequest) -> PlanDraft:
        data, files, usage = await self._run("plan", plan_prompt(request), PLAN_SCHEMA, GROUND_RULES)
        return PlanDraft(list(data.get("pages", [])), list(data.get("paths", [])), files, _cost(usage), usage)

    async def write_page(self, request: PageRequest) -> PageDraft:
        data, files, usage = await self._run(
            "write", page_prompt(request), PAGE_SCHEMA, GROUND_RULES + "\n" + PAGE_SYNTAX
        )
        return PageDraft(str(data.get("body", "")), list(data.get("checks", [])), files, _cost(usage), usage)

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        data, files, usage = await self._run(
            "digest", digest_prompt(request), DIGEST_SCHEMA, GROUND_RULES + "\n" + PAGE_SYNTAX
        )
        return DigestDraft(str(data.get("title", "")), str(data.get("body", "")), files, _cost(usage), usage)

    async def grade(self, request: GradeRequest) -> Verdict:
        """Grades with no tools at all: only the check, the rubric, the page and the answer (design section 8.2)."""
        data, _files, usage = await self._run(
            "grade", grade_prompt(request), GRADE_SCHEMA, GRADE_RULES, tools=(), budget_usd=self.grade_budget_usd
        )
        missed = [str(item) for item in data.get("missed", [])]
        return Verdict(str(data.get("verdict", "")), missed, str(data.get("feedback", "")), _cost(usage), usage)

    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        """Streams the answer's text as Claude writes it, then a final chunk with the files read and the usage."""
        model = self.models["answer"]
        outcome = _Outcome()
        with self._call_files() as (settings_file, read_log):
            command = self._command(model, None, GROUND_RULES + "\n" + PAGE_SYNTAX + "\n" + ANSWER_RULES,
                                    ALLOWED_TOOLS, self.answer_limits[0], self.answer_limits[1], settings_file,
                                    partial=True)  # fmt: skip
            lines = runner.run_program(command, neutralize(answer_prompt(request)), self.root, self.environment,
                                       self.settings.timeout_seconds)  # fmt: skip
            async with aclosing(lines):
                async for event in _events(lines):
                    text = _text_delta(event)
                    if text:
                        yield AnswerChunk(text=text)
                    _note(event, outcome)
            result = _finished(outcome)
            usage = _usage(result, model, outcome)
            yield AnswerChunk(done=True, files_read=_read(read_log), cost_usd=_cost(usage), usage=usage)

    async def _run(
        self,
        kind: str,
        prompt: str,
        schema: dict[str, Any],
        system_prompt: str,
        tools: tuple[str, ...] = ALLOWED_TOOLS,
        budget_usd: float | None = None,
    ) -> tuple[dict[str, Any], list[str], Usage]:
        model = self.models[kind]
        budget = self.limits.max_budget_usd_per_call if budget_usd is None else budget_usd
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                await anyio.sleep(2)
            outcome = _Outcome()
            with self._call_files() as (settings_file, read_log):
                command = self._command(model, schema, system_prompt, tools, self.limits.max_turns, budget,
                                        settings_file)  # fmt: skip
                try:
                    lines = runner.run_program(command, neutralize(prompt), self.root, self.environment,
                                               self.settings.timeout_seconds)  # fmt: skip
                    async with aclosing(lines):
                        async for event in _events(lines):
                            _note(event, outcome)
                except AssistantError as error:
                    if outcome.result is not None:
                        raise
                    last_error = _ProgramFailed(str(error))
                    continue
                result = _finished(outcome)
                data = result.get("structured_output")
                if not isinstance(data, dict):
                    raise AssistantError("Claude Code finished without a structured answer.")
                return data, _read(read_log), _usage(result, model, outcome)
        raise last_error or AssistantError("Claude Code couldn't be run.")

    def _command(
        self,
        model: str,
        schema: dict[str, Any] | None,
        system_prompt: str,
        tools: tuple[str, ...],
        max_turns: int,
        budget_usd: float,
        settings_file: Path,
        partial: bool = False,
    ) -> list[str]:
        command = [
            self.program, "-p", "--output-format", "stream-json", "--verbose",
            "--tools", ",".join(tools),
            "--permission-mode", "dontAsk",
            "--setting-sources", "",
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
            "--settings", str(settings_file),
            "--max-turns", str(max_turns),
            "--max-budget-usd", f"{budget_usd:g}",
            "--model", model,
            "--system-prompt", system_prompt,
        ]  # fmt: skip
        if tools:
            # Reads are allowed only under the working folder; dontAsk refuses everything else, hook or not.
            command += ["--allowedTools", *(f"{tool}(./**)" for tool in tools)]
        if schema is not None:
            command += ["--json-schema", json.dumps(schema)]
        if partial:
            command.append("--include-partial-messages")
        return command

    @contextmanager
    def _call_files(self) -> Iterator[tuple[Path, Path]]:
        """The settings file with the guard hook, and the hook's read log: private, outside `source/`, removed after."""
        with tempfile.TemporaryDirectory(prefix="codetrail-call-") as folder:
            read_log = Path(folder) / "reads.log"
            # -I: the hook must import Codetrail's guard, never a `codetrail` folder in `source/` (the working folder).
            parts = (sys.executable, "-I", "-m", "codetrail.assistant.guard_hook", str(self.root), str(read_log))
            hook = " ".join(shlex.quote(part) for part in parts)
            settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
                {"type": "command", "command": hook, "timeout": self.settings.hook_timeout_seconds}]}]}}  # fmt: skip
            settings_file = Path(folder) / "settings.json"
            descriptor = os.open(settings_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(settings, handle)
            yield settings_file, read_log


async def _events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """The stream's JSON events; a line that isn't a JSON object is skipped."""
    async for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def _note(event: dict[str, Any], outcome: _Outcome) -> None:
    if event.get("type") == "result":
        outcome.result = event
    elif event.get("type") == "rate_limit_event":
        windows = (event.get("rate_limit_info") or {}).get("unifiedWindows") or {}
        for name, window in windows.items():
            if isinstance(window, dict):
                try:
                    outcome.windows[str(name)] = PlanWindow(str(name), float(window["utilization"]),
                                                            int(window["resetsAt"]))  # fmt: skip
                except KeyError, TypeError, ValueError:
                    continue


def _text_delta(event: dict[str, Any]) -> str:
    if event.get("type") != "stream_event":
        return ""
    inner = event.get("event") or {}
    delta = inner.get("delta") or {}
    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
        return str(delta.get("text", ""))
    return ""


def _finished(outcome: _Outcome) -> dict[str, Any]:
    result = outcome.result
    if result is None:
        raise AssistantError("Claude Code ended without a result.")
    if result.get("is_error"):
        raise AssistantError(f"Claude Code didn't finish the task ({result.get('subtype', 'error')}).")
    return result


def _usage(result: dict[str, Any], model: str, outcome: _Outcome) -> Usage:
    usage = result.get("usage") or {}

    def count(key: str) -> int:
        value = usage.get(key, 0)
        return int(value) if isinstance(value, int | float) else 0

    cost = result.get("total_cost_usd")
    return Usage(
        PROVIDER, model,
        input_tokens=count("input_tokens") + count("cache_creation_input_tokens"),
        cached_input_tokens=count("cache_read_input_tokens"),
        output_tokens=count("output_tokens"),
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
        plan_windows=tuple(outcome.windows.values()),
    )  # fmt: skip


def _cost(usage: Usage) -> float:
    return usage.cost_usd or 0.0


def _read(read_log: Path) -> list[str]:
    if not read_log.exists():
        return []
    return list(dict.fromkeys(line for line in read_log.read_text(encoding="utf-8").splitlines() if line))
