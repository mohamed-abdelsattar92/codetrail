"""The codex adapter: the reader's own `codex exec`, in a read-only sandbox, on the reader's sign-in (design 15.1).

Codex's tools are shell commands, and its read-only sandbox prevents writes, not reads, so Codetrail can't confine it to
`source/`; targets must opt in to it (section 15.5). Each call runs with an empty temporary `HOME`, `/bin/sh`, no
`USER` or `LOGNAME`, the reader's `CODEX_HOME` for the sign-in, and `-c` overrides that switch off the reader's MCP
servers, notifications and shell environment and the target's `AGENTS.md`. The prompt goes in on stdin; structured
answers follow `--output-schema`; tokens come from `turn.completed` events. Answers are buffered, not streamed, so the
bridge can scan the whole text before the reader sees it.
"""

from __future__ import annotations

import json
import os
import shlex
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
    QuestionRequest,
    Usage,
    Verdict,
    runner,
)
from codetrail.assistant.environment import child_environment, resolve_program
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
from codetrail.config import CodexSettings, GenerationSettings

PROVIDER: Final = "codex"
# Switch off what the reader's Codex configuration and the target's instructions could add (design section 15.5).
ISOLATION = ("mcp_servers={}", "notify=[]", "project_doc_max_bytes=0", 'shell_environment_policy.inherit="none"')


class _ProgramFailed(AssistantError):
    """The program ended without an answer: worth another try."""


@dataclass
class _Outcome:
    message: str | None = None
    failure: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    files_read: list[str] = field(default_factory=list)


class CodexAssistant:
    def __init__(
        self,
        source_root: Path,
        models: Mapping[str, str],
        settings: CodexSettings,
        limits: GenerationSettings,
        retries: int = 2,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self.root = source_root.resolve()
        self.models = dict(models)
        self.settings = settings
        self.limits = limits
        self.retries = retries
        self.environ = dict(environ)
        self.program = resolve_program(settings.command, self.environ.get("PATH", ""))

    async def plan(self, request: PlanRequest) -> PlanDraft:
        data, outcome, usage = await self._run("plan", GROUND_RULES, plan_prompt(request), PLAN_SCHEMA)
        return PlanDraft(list(data.get("pages", [])), list(data.get("paths", [])), outcome.files_read, 0.0, usage)

    async def write_page(self, request: PageRequest) -> PageDraft:
        data, outcome, usage = await self._run(
            "write", GROUND_RULES + "\n" + PAGE_SYNTAX, page_prompt(request), PAGE_SCHEMA
        )
        return PageDraft(str(data.get("body", "")), list(data.get("checks", [])), outcome.files_read, 0.0, usage)

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        data, outcome, usage = await self._run(
            "digest", GROUND_RULES + "\n" + PAGE_SYNTAX, digest_prompt(request), DIGEST_SCHEMA
        )
        return DigestDraft(str(data.get("title", "")), str(data.get("body", "")), outcome.files_read, 0.0, usage)

    async def grade(self, request: GradeRequest) -> Verdict:
        data, _outcome, usage = await self._run("grade", GRADE_RULES, grade_prompt(request), GRADE_SCHEMA)
        missed = [str(item) for item in data.get("missed", [])]
        return Verdict(str(data.get("verdict", "")), missed, str(data.get("feedback", "")), 0.0, usage)

    async def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        outcome = await self._call(
            "answer", GROUND_RULES + "\n" + PAGE_SYNTAX + "\n" + ANSWER_RULES, answer_prompt(request), None
        )
        yield AnswerChunk(text=outcome.message or "")
        yield AnswerChunk(done=True, files_read=outcome.files_read, usage=self._usage("answer", outcome))

    async def _run(
        self, kind: str, rules: str, prompt: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], _Outcome, Usage]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                await anyio.sleep(2)
            try:
                outcome = await self._call(kind, rules, prompt, schema)
            except _ProgramFailed as error:
                last_error = error
                continue
            try:
                data = json.loads(outcome.message or "")
            except ValueError as error:
                raise AssistantError("Codex's answer wasn't the JSON it was asked for.") from error
            if not isinstance(data, dict):
                raise AssistantError("Codex's answer wasn't a JSON object.")
            return data, outcome, self._usage(kind, outcome)
        raise last_error or AssistantError("Codex couldn't be run.")

    async def _call(self, kind: str, rules: str, prompt: str, schema: dict[str, Any] | None) -> _Outcome:
        outcome = _Outcome()
        with self._call_folder() as folder:
            environment = child_environment(PROVIDER, self.settings.auth, self.environ, empty_home=folder / "home")
            command = [
                self.program,
                "exec",
                "-",
                "--sandbox",
                "read-only",
                "--json",
                "--ephemeral",
                "--skip-git-repo-check",
                "-C",
                str(self.root),
            ]
            for override in ISOLATION:
                command += ["-c", override]
            model = self.models.get(kind, "")
            if model:
                command += ["-m", model]
            if schema is not None:
                schema_file = folder / "schema.json"
                schema_file.write_text(json.dumps(schema), encoding="utf-8")
                command += ["--output-schema", str(schema_file)]
            # Codex has no system prompt flag; Codetrail's rules lead the prompt, above the task.
            text = neutralize(f"{rules}\n\n{prompt}")
            try:
                lines = runner.run_program(command, text, self.root, environment, self.settings.timeout_seconds)
                async with aclosing(lines):
                    async for line in lines:
                        self._note(line, outcome)
            except _LimitPassed:
                raise
            except AssistantError as error:
                if outcome.message is None:
                    raise _ProgramFailed(str(error)) from error
                raise
        if outcome.failure is not None:
            raise AssistantError(f"Codex didn't finish the task: {outcome.failure[:300]}")
        if outcome.message is None:
            raise _ProgramFailed("Codex ended without an answer.")
        return outcome

    def _note(self, line: str, outcome: _Outcome) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        found = event.get("item")
        item: dict[str, Any] = found if isinstance(found, dict) else {}
        if kind == "item.completed" and item.get("type") == "agent_message":
            outcome.message = str(item.get("text", ""))
        elif kind == "item.completed" and item.get("type") == "command_execution":
            for path in self._paths_in(str(item.get("command", ""))):
                if path not in outcome.files_read:
                    outcome.files_read.append(path)
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
            outcome.input_tokens += _count(usage, "input_tokens")
            outcome.cached_input_tokens += _count(usage, "cached_input_tokens")
            outcome.output_tokens += _count(usage, "output_tokens")
            if outcome.input_tokens + outcome.output_tokens > self.settings.max_tokens_per_call:
                raise _LimitPassed(
                    f"Codex passed its token limit ({self.settings.max_tokens_per_call}) and was stopped."
                )
        elif kind == "turn.failed":
            outcome.failure = str((event.get("error") or {}).get("message", "the turn failed"))
        elif kind == "error":
            outcome.failure = str(event.get("message", "an error"))

    def _paths_in(self, command: str) -> list[str]:
        """Files inside `source/` that a command names: a best-effort record of what Codex read."""
        words = _words(command)
        words += [inner for word in words if " " in word for inner in _words(word)]  # bash -lc '<script>'
        found = []
        for word in words:
            candidate = Path(word) if os.path.isabs(word) else self.root / word
            candidate = candidate.resolve()
            if candidate.is_relative_to(self.root) and candidate.is_file():
                found.append(candidate.relative_to(self.root).as_posix())
        return found

    def _usage(self, kind: str, outcome: _Outcome) -> Usage:
        return Usage(
            PROVIDER,
            self.models.get(kind, ""),
            outcome.input_tokens - outcome.cached_input_tokens,
            outcome.cached_input_tokens,
            outcome.output_tokens,
        )

    @contextmanager
    def _call_folder(self) -> Iterator[Path]:
        """A private folder outside `source/` for the schema file and the empty `HOME`, removed after the call."""
        with tempfile.TemporaryDirectory(prefix="codetrail-codex-") as folder:
            (Path(folder) / "home").mkdir(mode=0o700)
            yield Path(folder)


class _LimitPassed(AssistantError):
    """The call passed its token limit; not worth another try."""


def _words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _count(usage: Mapping[str, Any], key: str) -> int:
    value = usage.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0
