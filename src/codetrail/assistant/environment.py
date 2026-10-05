"""The environment a provider's program starts with: an allowlist, so no key or token leaks in (design 15.2).

With `subscription`, no key variable is passed, so the program can only use its own saved sign-in. With `api_key`, the
provider's key variables pass by name; Codetrail never reads their values. Codex also gets an empty `HOME` and a plain
shell, so no profile runs and `~` leads nowhere (section 15.5). Relative `PATH` entries are dropped, and programs are
resolved to absolute paths before they start.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from codetrail.assistant import AssistantError

Provider = Literal["claude_code", "codex"]
Auth = Literal["subscription", "api_key"]

# Claude Code's sign-in on macOS lives in the Keychain, which needs USER and LOGNAME (checked on 5 October 2026).
CLAUDE_NAMES = ("HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM", "CLAUDE_CONFIG_DIR")
CODEX_NAMES = ("LANG", "TMPDIR", "TERM")
KEY_NAMES: dict[Provider, tuple[str, ...]] = {
    "claude_code": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY", "CODEX_API_KEY"),
}


def safe_path(value: str) -> str:
    """`PATH` without empty or relative entries, which would resolve against the working folder (`source/`)."""
    return os.pathsep.join(entry for entry in value.split(os.pathsep) if entry and os.path.isabs(entry))


def child_environment(
    provider: Provider, auth: Auth, environ: Mapping[str, str] = os.environ, empty_home: Path | None = None
) -> dict[str, str]:
    names = CLAUDE_NAMES if provider == "claude_code" else CODEX_NAMES
    environment = {name: environ[name] for name in names if name in environ}
    environment |= {name: value for name, value in environ.items() if name.startswith("LC_")}
    environment["PATH"] = safe_path(environ.get("PATH", ""))
    if provider == "codex":
        if empty_home is None:
            raise AssistantError("Codex needs an empty folder for its HOME.")
        codex_home = environ.get("CODEX_HOME") or str(Path(environ.get("HOME", str(Path.home()))) / ".codex")
        if not os.path.isabs(codex_home):
            raise AssistantError(f"CODEX_HOME must be an absolute path, not {codex_home!r}.")
        environment |= {"HOME": str(empty_home), "SHELL": "/bin/sh", "CODEX_HOME": codex_home}
    if auth == "api_key":
        environment |= {name: environ[name] for name in KEY_NAMES[provider] if name in environ}
    return environment


def resolve_program(command: str, path: str) -> str:
    """The program's absolute path, found only through absolute `PATH` entries."""
    if os.sep in command:
        if not os.path.isabs(command):
            raise AssistantError(f"The program {command!r} must be a name on PATH or an absolute path.")
        if not os.access(command, os.X_OK):
            raise AssistantError(f"{command} isn't installed or isn't executable.")
        return command
    found = shutil.which(command, path=safe_path(path))
    if found is None:
        raise AssistantError(f"{command} isn't installed (it isn't on PATH).")
    return found
