"""Sends each kind of call to the provider and model configured for it (design section 15.1).

`build_assistant` makes one adapter per provider the target's `[models]` use, each knowing the models of its kinds,
and a router that the rest of Codetrail uses as its one assistant.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Final

from codetrail.assistant import (
    AnswerChunk,
    Assistant,
    AssistantError,
    DigestDraft,
    DigestRequest,
    GradeRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
    QuestionRequest,
    Verdict,
)
from codetrail.config import GlobalConfig, TargetConfig, model_choice

KINDS: Final = ("plan", "write", "digest", "answer", "grade")


class RoutedAssistant:
    def __init__(self, routes: Mapping[str, Assistant]) -> None:
        self.routes = dict(routes)

    async def plan(self, request: PlanRequest) -> PlanDraft:
        return await self.routes["plan"].plan(request)

    async def write_page(self, request: PageRequest) -> PageDraft:
        return await self.routes["write"].write_page(request)

    async def write_digest(self, request: DigestRequest) -> DigestDraft:
        return await self.routes["digest"].write_digest(request)

    def answer(self, request: QuestionRequest) -> AsyncIterator[AnswerChunk]:
        return self.routes["answer"].answer(request)

    async def grade(self, request: GradeRequest) -> Verdict:
        return await self.routes["grade"].grade(request)


def build_assistant(
    source_root: Path, settings: GlobalConfig, target: TargetConfig, environ: Mapping[str, str] = os.environ
) -> RoutedAssistant:
    from codetrail.assistant.claude_code import ClaudeCodeAssistant
    from codetrail.assistant.codex import CodexAssistant
    from codetrail.assistant.local import LocalAssistant

    choices = {kind: model_choice(getattr(target.models, kind)) for kind in KINDS}
    models_by_provider: dict[str, dict[str, str]] = {}
    for kind, (provider, model) in choices.items():
        models_by_provider.setdefault(provider, {})[kind] = model
    adapters: dict[str, Assistant] = {}
    retries = settings.assistant.retry_attempts
    for provider, models in models_by_provider.items():
        if provider == "claude_code":
            adapters[provider] = ClaudeCodeAssistant(
                source_root,
                models,
                settings.providers.claude_code,
                target.generation,
                retries,
                (settings.bridge.max_turns, settings.bridge.max_budget_usd),
                settings.learn.max_budget_usd,
                environ,
            )
        elif provider == "codex":
            if not target.assistant.allow_codex:
                raise AssistantError("This target doesn't allow Codex; set [assistant] allow_codex = true to use it.")
            adapters[provider] = CodexAssistant(
                source_root, models, settings.providers.codex, target.generation, retries, environ
            )
        else:
            adapters[provider] = LocalAssistant(source_root, models, settings.providers.local, target.generation)
    return RoutedAssistant({kind: adapters[provider] for kind, (provider, _model) in choices.items()})
