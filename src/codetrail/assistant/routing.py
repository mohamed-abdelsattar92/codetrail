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
    OverviewDraft,
    OverviewRequest,
    PageDraft,
    PageRequest,
    PlanDraft,
    PlanRequest,
    QuestionRequest,
    Verdict,
)
from codetrail.config import GenerationSettings, GlobalConfig, TargetConfig, model_choice

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

    async def write_overview(self, request: OverviewRequest) -> OverviewDraft:
        route = self.routes.get("overview")
        if route is None:
            raise AssistantError("No assistant is set up for the team's overview.")
        return await route.write_overview(request)


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
    if "codex" in models_by_provider and not target.assistant.allow_codex:  # policy first, whatever is installed
        raise AssistantError("This target doesn't allow Codex; set [assistant] allow_codex = true to use it.")
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
            adapters[provider] = CodexAssistant(
                source_root, models, settings.providers.codex, target.generation, retries, environ
            )
        else:
            adapters[provider] = LocalAssistant(source_root, models, settings.providers.local, target.generation)
    return RoutedAssistant({kind: adapters[provider] for kind, (provider, _model) in choices.items()})


def build_overview_assistant(
    workspace: Path,
    settings: GlobalConfig,
    model: str,
    max_budget_usd_per_call: float,
    environ: Mapping[str, str] = os.environ,
) -> Assistant:
    """The adapter for a team's overview (design section 24.9): `model` is `[models] overview`,
    `max_budget_usd_per_call` is `[catch_up] max_budget_usd_per_call`, and `workspace` an empty folder the caller
    made, since the call has no tools and reads nothing."""
    from codetrail.assistant.claude_code import ClaudeCodeAssistant
    from codetrail.assistant.local import LocalAssistant

    provider, name = model_choice(model)
    models, limits = {"overview": name}, GenerationSettings(max_budget_usd_per_call=max_budget_usd_per_call)
    if provider == "claude_code":
        return ClaudeCodeAssistant(
            workspace,
            models,
            settings.providers.claude_code,
            limits,
            settings.assistant.retry_attempts,
            (settings.bridge.max_turns, settings.bridge.max_budget_usd),
            settings.learn.max_budget_usd,
            environ,
        )
    if provider == "local":
        return LocalAssistant(workspace, models, settings.providers.local, limits)
    raise AssistantError("The team's overview can't use Codex.")
