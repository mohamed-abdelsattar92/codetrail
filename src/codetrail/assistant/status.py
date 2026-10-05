"""Whether each provider is installed and signed in, and how, before any paid work (design section 15.2).

Claude Code is asked with `claude auth status --json` and Codex with `codex login status`, in the same allowlisted
environment the calls use; only whether the program is signed in, and how, is kept, never an account's details. With
`auth = "subscription"`, a sign-in that bills an API account instead is refused. The local provider's endpoint is asked
for its models, which are shown so a stranger answering on the port is noticed.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from codetrail.assistant import AssistantError, runner
from codetrail.assistant.environment import child_environment, resolve_program
from codetrail.config import GlobalConfig, TargetConfig, model_choice

CHECK_TIMEOUT_SECONDS = 30
INSTALL = {
    "claude_code": "Install Claude Code (https://code.claude.com/docs/en/setup), run `claude` once and sign in with "
    "/login using your Claude subscription.",
    "codex": "Install Codex (`npm install -g @openai/codex`), then run `codex login` and choose Sign in with ChatGPT.",
}
CODEX_WARNING = (
    "Codex reads outside the repository's allowed files (design section 15.5). Its isolation from your Codex "
    "settings is tested against a stand-in, and checked live only where Codex is installed (`just test-live`)."
)


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    ready: bool
    installed: bool = False
    signed_in: bool = False
    program: str = ""
    method: str = ""
    fix: str = ""
    warning: str = ""
    models: tuple[str, ...] = ()


def provider_status(
    provider: str, settings: GlobalConfig, environ: Mapping[str, str] = os.environ, client: httpx.Client | None = None
) -> ProviderStatus:
    if provider == "claude_code":
        return _claude_code(settings, environ)
    if provider == "codex":
        return _codex(settings, environ)
    return _local(settings, client)


def require_ready(
    target: TargetConfig,
    settings: GlobalConfig,
    environ: Mapping[str, str] = os.environ,
    kinds: Sequence[str] = ("plan", "write", "digest", "answer", "grade"),
) -> list[ProviderStatus]:
    """The status of each provider the given kinds of call use; raises with the fix when one isn't ready."""
    providers = list(dict.fromkeys(model_choice(getattr(target.models, kind))[0] for kind in kinds))
    statuses = [provider_status(provider, settings, environ) for provider in providers]
    for status in statuses:
        if not status.ready:
            raise AssistantError(f"{status.provider} isn't ready. {status.fix}")
    return statuses


def _claude_code(settings: GlobalConfig, environ: Mapping[str, str]) -> ProviderStatus:
    config = settings.providers.claude_code
    environment = child_environment("claude_code", config.auth, environ)
    try:
        program = resolve_program(config.command, environment.get("PATH", ""))
    except AssistantError:
        return ProviderStatus("claude_code", False, fix=INSTALL["claude_code"])
    with tempfile.TemporaryDirectory() as folder:
        try:
            _code, output = runner.run_command(
                [program, "auth", "status", "--json"], Path(folder), environment, CHECK_TIMEOUT_SECONDS
            )
            found = json.loads(output)
        except AssistantError, ValueError:
            return ProviderStatus(
                "claude_code",
                False,
                True,
                program=program,
                fix="`claude auth status` didn't answer; run `claude` once to finish its setup.",
            )
    found = found if isinstance(found, dict) else {}
    signed_in = found.get("loggedIn") is True
    subscription = found.get("authMethod") == "claude.ai"
    plan = str(found.get("subscriptionType") or "")
    method = (
        f"Claude subscription ({plan})"
        if subscription and plan
        else ("Claude subscription" if subscription else f"{found.get('authMethod', 'unknown')} sign-in")
    )
    if not signed_in:
        return ProviderStatus(
            "claude_code",
            False,
            True,
            False,
            program,
            fix="Run `claude` and sign in with /login using your Claude subscription.",
        )
    if config.auth == "subscription" and not subscription:
        return ProviderStatus(
            "claude_code",
            False,
            True,
            True,
            program,
            method,
            fix="Claude Code isn't signed in with a Claude subscription: run `claude` and /login "
            'with your Claude account, or set [providers.claude_code] auth = "api_key".',
        )
    return ProviderStatus("claude_code", True, True, True, program, method)


def _codex(settings: GlobalConfig, environ: Mapping[str, str]) -> ProviderStatus:
    config = settings.providers.codex
    with tempfile.TemporaryDirectory() as folder:
        home = Path(folder) / "home"
        home.mkdir()
        environment = child_environment("codex", config.auth, environ, empty_home=home)
        try:
            program = resolve_program(config.command, environment.get("PATH", ""))
        except AssistantError:
            return ProviderStatus("codex", False, fix=INSTALL["codex"], warning=CODEX_WARNING)
        try:
            code, output = runner.run_command(
                [program, "login", "status"], Path(folder), environment, CHECK_TIMEOUT_SECONDS
            )
        except AssistantError:
            code, output = 1, ""
    signed_in = code == 0 and "logged in" in output.lower() and "not logged in" not in output.lower()
    subscription = "chatgpt" in output.lower()
    method = "ChatGPT subscription" if subscription else "API key"
    if not signed_in:
        return ProviderStatus(
            "codex",
            False,
            True,
            False,
            program,
            fix="Run `codex login` and choose Sign in with ChatGPT.",
            warning=CODEX_WARNING,
        )
    if config.auth == "subscription" and not subscription:
        return ProviderStatus(
            "codex",
            False,
            True,
            True,
            program,
            method,
            fix="Codex is signed in with an API key: run `codex login` and choose Sign in with "
            'ChatGPT, or set [providers.codex] auth = "api_key".',
            warning=CODEX_WARNING,
        )
    return ProviderStatus("codex", True, True, True, program, method, warning=CODEX_WARNING)


def _local(settings: GlobalConfig, client: httpx.Client | None) -> ProviderStatus:
    config = settings.providers.local
    fix = f"Start Ollama (`ollama serve`) or LM Studio's server, or set [providers.local] base_url ({config.base_url})."
    http = client or httpx.Client(trust_env=False, follow_redirects=False, timeout=10)
    try:
        response = http.get(config.base_url.rstrip("/") + "/models")
        listed = response.json().get("data", []) if response.status_code == 200 else None
    except httpx.HTTPError, ValueError, AttributeError:
        listed = None
    finally:
        if client is None:
            http.close()
    if not isinstance(listed, list):
        return ProviderStatus("local", False, fix=fix, program=config.base_url)
    models = tuple(str(item.get("id")) for item in listed if isinstance(item, dict) and item.get("id"))
    return ProviderStatus("local", True, True, True, config.base_url, "no sign-in needed", models=models)
