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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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


class BridgeSettings(Settings):
    max_question_chars: int = Field(default=4000, gt=0, le=20_000)
    max_turns: int = Field(default=20, gt=0)
    max_budget_usd: float = Field(default=1.0, gt=0)


class LearnSettings(Settings):
    grading_cooldown_seconds: int = Field(default=10, ge=0)
    max_budget_usd: float = Field(default=0.25, gt=0)


class ClaudeSettings(Settings):
    retry_attempts: int = Field(default=2, ge=0, le=5)


class ExtractSettings(Settings):
    max_file_bytes: int = Field(default=1_000_000, gt=0)


class ServerSettings(Settings):
    # The host is always 127.0.0.1 and can't be configured (design section 7.4).
    port: int = Field(default=8765, ge=1024, le=65535)
    login_code_ttl_seconds: int = Field(default=60, gt=0)
    session_minutes: int = Field(default=480, gt=0)
    update_cooldown_seconds: int = Field(default=300, ge=0)


class InterfaceSettings(Settings):
    default_language: str = "en"


class SignalSettings(Settings):
    cache_seconds: int = Field(default=60, ge=0)


class DiagramSettings(Settings):
    max_nodes: int = Field(default=25, gt=0)


class GlobalConfig(Settings):
    tools: ToolsSettings = ToolsSettings()
    claude: ClaudeSettings = ClaudeSettings()
    bridge: BridgeSettings = BridgeSettings()
    learn: LearnSettings = LearnSettings()
    extract: ExtractSettings = ExtractSettings()
    server: ServerSettings = ServerSettings()
    ui: InterfaceSettings = InterfaceSettings()
    signal: SignalSettings = SignalSettings()
    diagrams: DiagramSettings = DiagramSettings()


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


class ModelSettings(Settings):
    plan: str = "claude-opus-5-5"
    write: str = "claude-sonnet-5-5"
    digest: str = "claude-sonnet-5-5"
    answer: str = "claude-sonnet-5-5"
    grade: str = "claude-sonnet-5-5"


class TargetConfig(Settings):
    repository: Path
    branch: str
    extractors: list[Literal["python", "adr", "openapi", "terraform", "swift"]] = [
        "python", "adr", "openapi", "terraform", "swift",
    ]  # fmt: skip
    adr: AdrSettings = AdrSettings()
    openapi: OpenApiSettings = OpenApiSettings()
    generation: GenerationSettings = GenerationSettings()
    models: ModelSettings = ModelSettings()

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
