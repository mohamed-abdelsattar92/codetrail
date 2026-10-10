"""Codetrail's configuration and folders (design sections 2.3 and 9).

The global settings live in `<config>/config.toml` and one file per target in `<config>/targets/<name>.toml`. Both are
validated at load time; unknown keys are refused by name, and a key that is absent takes its default.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from codetrail.errors import CodetrailError

TARGET_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


@dataclass(frozen=True)
class Paths:
    """Where Codetrail keeps its configuration, data and state, following the XDG base directories."""

    config_dir: Path
    data_dir: Path
    state_dir: Path

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> Paths:
        home = Path(environ.get("HOME") or Path.home())

        def base(variable: str, default: Path) -> Path:
            value = environ.get(variable)
            # The XDG specification says relative values are invalid and must be ignored.
            return Path(value) if value and Path(value).is_absolute() else default

        return cls(
            config_dir=base("XDG_CONFIG_HOME", home / ".config") / "codetrail",
            data_dir=base("XDG_DATA_HOME", home / ".local" / "share") / "codetrail",
            state_dir=base("XDG_STATE_HOME", home / ".local" / "state") / "codetrail",
        )

    def target_file(self, name: str) -> Path:
        return self.config_dir / "targets" / f"{name}.toml"

    def ignore_file(self, name: str) -> Path:
        return self.config_dir / "targets" / f"{name}.ignore"

    def target_data(self, name: str) -> Path:
        return self.data_dir / name

    def target_state(self, name: str) -> Path:
        return self.state_dir / name


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ToolsSettings(Settings):
    gitleaks: str = "gitleaks"
    gitleaks_timeout_seconds: float = Field(default=600, gt=0, le=86_400)  # each gitleaks and `mise which` run


class BridgeSettings(Settings):
    max_question_chars: int = Field(default=4000, gt=0, le=20_000)
    max_turns: int = Field(default=20, gt=0)
    max_budget_usd: float = Field(default=1.0, gt=0)
    max_session_answers: int = Field(default=20, gt=0, le=200)  # unsaved answers kept per session (16.4)


class SearchSettings(Settings):
    max_query_chars: int = Field(default=200, gt=0, le=1000)
    max_results: int = Field(default=20, gt=0, le=100)


class LearnSettings(Settings):
    grading_cooldown_seconds: int = Field(default=10, ge=0)
    max_budget_usd: float = Field(default=0.25, gt=0)


class AssistantSettings(Settings):
    retry_attempts: int = Field(default=2, ge=0, le=5)


class ClaudeCodeSettings(Settings):
    command: str = "claude"
    auth: Literal["subscription", "api_key"] = "subscription"
    timeout_seconds: int = Field(default=900, gt=0)
    hook_timeout_seconds: int = Field(default=30, gt=0)


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class LocalSettings(Settings):
    base_url: str = "http://127.0.0.1:11434/v1"  # Ollama; LM Studio is http://127.0.0.1:1234/v1
    max_turns: int = Field(default=30, gt=0)
    max_tokens_per_call: int = Field(default=200_000, gt=0)
    timeout_seconds: int = Field(default=900, gt=0)
    max_read_bytes: int = Field(default=200_000, gt=0)

    @field_validator("base_url")
    @classmethod
    def loopback_only(cls, base_url: str) -> str:
        """Prompts carry the repository's text, so they go only to this machine (design section 15.5)."""
        parts = urlsplit(base_url)
        if (
            parts.scheme not in ("http", "https")
            or parts.username
            or parts.password
            or (parts.hostname not in LOOPBACK_HOSTS)
        ):
            raise ValueError(f"{base_url!r} isn't a loopback address (127.0.0.1, ::1 or localhost, with no user).")
        return base_url


class CodexSettings(Settings):
    command: str = "codex"
    auth: Literal["subscription", "api_key"] = "subscription"
    max_tokens_per_call: int = Field(default=400_000, gt=0)
    timeout_seconds: int = Field(default=900, gt=0)


class ExtractSettings(Settings):
    max_file_bytes: int = Field(default=1_000_000, gt=0)
    max_attribute_chars: int = Field(default=300, ge=10)
    max_tsconfig_paths: int = Field(default=100, gt=0, le=10_000)  # path patterns and targets read from a tsconfig
    max_workflow_steps: int = Field(default=5000, gt=0, le=100_000)  # steps read from one workflow file
    max_resource_paths: int = Field(default=20, gt=0, le=1000)  # path attributes kept on one Terraform resource


class ServerSettings(Settings):
    # The host is always 127.0.0.1 and can't be configured (design section 7.4).
    port: int = Field(default=8765, ge=1024, le=65535)
    login_code_ttl_seconds: int = Field(default=60, gt=0)
    session_minutes: int = Field(default=480, gt=0)
    update_cooldown_seconds: int = Field(default=300, ge=0)
    estimate_ttl_seconds: int = Field(default=300, gt=0)
    update_log_lines: int = Field(default=200, gt=0)  # the update panel's steps the server keeps


class InterfaceSettings(Settings):
    default_language: str = "en"


class SignalSettings(Settings):
    cache_seconds: int = Field(default=60, ge=0)


class DiagramSettings(Settings):
    max_nodes: int = Field(default=25, gt=0)


# A file name or suffix, lower-cased, and its language (design section 19.6); `[metrics.languages]` replaces it.
DEFAULT_LANGUAGES = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".astro": "Astro", ".swift": "Swift", ".kt": "Kotlin", ".kts": "Kotlin", ".java": "Java", ".go": "Go",
    ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++",
    ".cs": "C#", ".m": "Objective-C", ".mm": "Objective-C", ".scala": "Scala",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "CSS",
    ".vue": "Vue", ".svelte": "Svelte", ".tf": "Terraform", ".yaml": "YAML", ".yml": "YAML", ".json": "JSON",
    ".toml": "TOML", ".xml": "XML", ".proto": "Protocol Buffers", ".graphql": "GraphQL",
    "dockerfile": "Dockerfile", "makefile": "Makefile", "justfile": "just",
}  # fmt: skip


class MetricsSettings(Settings):
    """The documentation metrics' and repository statistics' limits (design sections 18.5 and 19.6)."""

    commit_window: int = Field(default=200, gt=0, le=10_000)  # the latest non-merge commits the commit metric reads
    proposed_adr_days: int = Field(default=30, ge=0)  # a proposed ADR older than this needs attention
    trend_updates: int = Field(default=12, ge=2, le=200)  # updates a trend line shows
    max_listed: int = Field(default=50, gt=0, le=5000)  # items a list shows before the rest fold away
    max_message_chars: int = Field(default=20_000, gt=0, le=1_000_000)  # of a commit body, matched for a why
    activity_months: int = Field(default=24, gt=0, le=600)  # months the activity bars show
    history_limit: int = Field(default=1_000_000, gt=0, le=100_000_000)  # commits whose dates are read
    largest_files: int = Field(default=10, gt=0, le=1000)  # files the Repository page lists by size
    churn_window: int = Field(default=500, gt=0, le=100_000)  # the latest non-merge commits the hot spots read
    quiet_days: int = Field(default=365, gt=0, le=36_500)  # code no commit changed for this long is quiet
    languages: dict[str, str] = DEFAULT_LANGUAGES

    @field_validator("languages")
    @classmethod
    def lower_case_names(cls, languages: dict[str, str]) -> dict[str, str]:
        return {name.lower(): language for name, language in languages.items()}


class ProvidersSettings(Settings):
    claude_code: ClaudeCodeSettings = ClaudeCodeSettings()
    codex: CodexSettings = CodexSettings()
    local: LocalSettings = LocalSettings()


class Price(Settings):
    """USD per million tokens, at API list prices: used for estimates and budgets only (design section 15.4)."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)


# API list prices on 5 October 2026 (https://platform.claude.com/docs/en/about-claude/pricing); they change, so a
# [prices] table in the configuration adds to or replaces these.
DEFAULT_PRICES = {
    "claude-opus-5-5": Price(input=4.0, output=20.0, cached_input=0.4),
    "claude-sonnet-5-5": Price(input=2.0, output=10.0, cached_input=0.2),
}


class TokenGuess(Settings):
    input: int = Field(ge=0)
    output: int = Field(ge=0)


class EstimateSettings(Settings):
    """Starting tokens per call, used until Codetrail has history of its own (design section 15.4)."""

    history_size: int = Field(default=20, ge=3)
    plan: TokenGuess = TokenGuess(input=60_000, output=8_000)
    write: TokenGuess = TokenGuess(input=120_000, output=6_000)
    revise: TokenGuess = TokenGuess(input=40_000, output=2_000)
    digest: TokenGuess = TokenGuess(input=40_000, output=3_000)
    answer: TokenGuess = TokenGuess(input=30_000, output=1_500)
    grade: TokenGuess = TokenGuess(input=4_000, output=500)


class GlobalConfig(Settings):
    tools: ToolsSettings = ToolsSettings()
    assistant: AssistantSettings = AssistantSettings()
    providers: ProvidersSettings = ProvidersSettings()
    prices: dict[str, Price] = DEFAULT_PRICES
    estimates: EstimateSettings = EstimateSettings()

    @field_validator("prices")
    @classmethod
    def keep_default_prices(cls, prices: dict[str, Price]) -> dict[str, Price]:
        return DEFAULT_PRICES | prices

    bridge: BridgeSettings = BridgeSettings()
    search: SearchSettings = SearchSettings()
    learn: LearnSettings = LearnSettings()
    extract: ExtractSettings = ExtractSettings()
    server: ServerSettings = ServerSettings()
    ui: InterfaceSettings = InterfaceSettings()
    signal: SignalSettings = SignalSettings()
    diagrams: DiagramSettings = DiagramSettings()
    metrics: MetricsSettings = MetricsSettings()


class AdrSettings(Settings):
    paths: list[str] = ["docs/adr/*.md"]


class OpenApiSettings(Settings):
    paths: list[str] = ["**/openapi.json", "**/openapi.yaml", "**/openapi.yml"]


class GenerationSettings(Settings):
    max_pages_per_update: int = Field(default=20, ge=0)
    concurrency: int = Field(default=2, gt=0, le=8)
    max_turns: int = Field(default=30, gt=0)
    max_budget_usd_per_call: float = Field(default=1.0, gt=0)
    max_budget_usd_per_update: float = Field(default=10.0, gt=0)
    # Counts every provider, including models with no configured price (design section 15.3).
    max_tokens_per_update: int = Field(default=5_000_000, gt=0)
    # An affected page with at most this many fact and link changes in its scope since it was written is revised:
    # only the sections the changes affect are rewritten (design section 6.3). 0 writes every page whole.
    revise_max_changes: int = Field(default=20, ge=0)


class TargetMetricsSettings(Settings):
    """What counts as a document, a commit that explains why, a test and a commit type here (sections 18.5, 19.6)."""

    document_globs: list[str] = ["README*", "*.md", "*.rst", "*.adoc", "*.txt", "docs/**"]  # gitignore syntax
    # Spaces and tabs only, never \s: whitespace that could cross lines would take quadratic time on a long run of
    # blank lines in a message.
    commit_why_pattern: str = r"(?im)^[ \t]*(?:#+[ \t]*)?why\b"

    @field_validator("commit_why_pattern")
    @classmethod
    def compiles(cls, pattern: str) -> str:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"isn't a valid regular expression: {error}") from error
        return pattern

    @property
    def why(self) -> re.Pattern[str]:
        return re.compile(self.commit_why_pattern)

    test_globs: list[str] = ["test/**", "tests/**", "**/test_*.py", "**/*_test.*", "**/*.test.*", "**/*.spec.*",
                             "**/__tests__/**", "**/Tests/**"]  # fmt: skip
    commit_type_pattern: str = r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\r\n]*)\))?!?:[ \t]"  # a Conventional Commit

    @field_validator("commit_type_pattern")
    @classmethod
    def has_a_type(cls, pattern: str) -> str:
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"isn't a valid regular expression: {error}") from error
        if "type" not in compiled.groupindex:
            raise ValueError("needs a (?P<type>...) group")
        return pattern

    @property
    def types(self) -> re.Pattern[str]:
        return re.compile(self.commit_type_pattern)


PROVIDERS = ("claude_code", "codex", "local")


def model_choice(value: str) -> tuple[str, str]:
    """A `[models]` value as (provider, model): `local:qwen3:14b` is ("local", "qwen3:14b"); no prefix: claude_code."""
    provider, separator, model = value.partition(":")
    if separator and provider in PROVIDERS:
        return provider, model
    return "claude_code", value


class ModelSettings(Settings):
    """The provider and model of each kind of call, as `provider:model` (design section 15.1)."""

    plan: str = "claude-opus-5-5"
    write: str = "claude-sonnet-5-5"
    digest: str = "claude-sonnet-5-5"
    answer: str = "claude-sonnet-5-5"
    grade: str = "claude-sonnet-5-5"

    @field_validator("plan", "write", "digest", "answer", "grade")
    @classmethod
    def known_provider(cls, value: str) -> str:
        provider, separator, _model = value.partition(":")
        if separator and provider not in PROVIDERS:
            raise ValueError(f"{provider!r} isn't a provider; use one of {', '.join(PROVIDERS)}.")
        return value

    def uses(self, provider: str) -> bool:
        return any(model_choice(getattr(self, kind))[0] == provider for kind in type(self).model_fields)


class TargetAssistantSettings(Settings):
    # Codex can read and run anything the reader can, so a target opts in to it (design section 15.5).
    allow_codex: bool = False


class TargetConfig(Settings):
    repository: Path
    branch: str
    extractors: list[Literal["python", "adr", "openapi", "terraform", "swift", "typescript", "github_actions"]] = [
        "python",
        "adr",
        "openapi",
        "terraform",
        "swift",
        "typescript",
        "github_actions",
    ]
    adr: AdrSettings = AdrSettings()
    openapi: OpenApiSettings = OpenApiSettings()
    generation: GenerationSettings = GenerationSettings()
    models: ModelSettings = ModelSettings()
    assistant: TargetAssistantSettings = TargetAssistantSettings()
    metrics: TargetMetricsSettings = TargetMetricsSettings()

    @model_validator(mode="after")
    def codex_needs_consent(self) -> TargetConfig:
        if self.models.uses("codex") and not self.assistant.allow_codex:
            raise ValueError(
                "models: Codex can read outside the repository's allowed files (design section 15.5); to use it for "
                "this target, set [assistant] allow_codex = true."
            )
        return self

    @field_validator("repository")
    @classmethod
    def expand_home(cls, repository: Path) -> Path:
        return repository.expanduser()


def validate_target_name(name: str) -> str:
    if not TARGET_NAME.fullmatch(name):
        raise CodetrailError(
            f"{name!r} isn't a valid target name: use lower-case letters, digits and hyphens, starting with a letter "
            "or digit, at most 63 characters."
        )
    return name


def load_global(paths: Paths) -> GlobalConfig:
    file = paths.config_dir / "config.toml"
    if not file.exists():
        return GlobalConfig()
    return _validate(GlobalConfig, _read_toml(file), file)


def load_target(paths: Paths, name: str) -> TargetConfig:
    file = paths.target_file(validate_target_name(name))
    if not file.exists():
        raise CodetrailError(f"No target named {name!r}. Add it with: codetrail target add {name} <path>")
    return _validate(TargetConfig, _read_toml(file), file)


def check_containment(paths: Paths, repository: Path) -> None:
    """Codetrail's folders and the repository must not contain each other (AGENTS.md, Never do, rule 6)."""
    repository = repository.expanduser().resolve()
    for folder in (paths.config_dir, paths.data_dir, paths.state_dir):
        folder = folder.expanduser().resolve()
        if folder.is_relative_to(repository) or repository.is_relative_to(folder):
            raise CodetrailError(
                f"Codetrail's folder {folder} can't be inside the repository, nor the repository inside it."
            )


def write_target(paths: Paths, name: str, repository: Path, branch: str) -> Path:
    """Writes a new target's configuration file and returns its path."""
    file = paths.target_file(validate_target_name(name))
    repository = repository.expanduser().resolve()
    check_containment(paths, repository)
    validate_branch_name(branch)
    # json.dumps with ASCII escapes writes a valid TOML basic string for any text.
    text = (
        f"# Codetrail target {name!r}, written by `codetrail target add`.\n"
        f"repository = {json.dumps(str(repository))}\n"
        f"branch = {json.dumps(branch)}\n"
    )
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file.open("x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError as error:
        raise CodetrailError(f"A target named {name!r} already exists: {file}") from error
    return file


def validate_branch_name(branch: str) -> None:
    from codetrail.repo.git import run_git  # the git runner, not the repository package's readers

    try:
        run_git(["check-ref-format", "--branch", branch])
    except CodetrailError as error:
        raise CodetrailError(f"{branch!r} isn't a valid branch name.") from error


def _read_toml(file: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise CodetrailError(f"{file} isn't valid TOML: {error}") from error


def _validate[T: Settings](model: type[T], data: dict[str, Any], file: Path) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in error.errors()
        )
        raise CodetrailError(f"{file}: {problems}") from error
