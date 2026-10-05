"""Scans for secrets with gitleaks (design section 3.4; ADR 0002).

gitleaks always runs with Codetrail's own configuration, ignores `gitleaks:allow` comments and `.gitleaksignore`
files, and never sees GITLEAKS_* settings, so nothing in a target can switch the scan off. Findings carry the path,
the rule and the line, never the value. A missing, failing or stuck gitleaks raises, so callers fail closed, with what
it printed. A mise shim is resolved to its binary first, since gitleaks runs in an empty folder where no version is set.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from codetrail.config import ToolsSettings
from codetrail.errors import CodetrailError
from codetrail.repo.rules import on_disk_key

COMMON_FLAGS = (
    "--report-format", "json", "--redact", "--no-banner", "--exit-code", "0", "--ignore-gitleaks-allow",
    "--gitleaks-ignore-path", os.devnull, "--log-level", "error",
)  # fmt: skip
POINT_AT_ANOTHER = "To run a particular gitleaks, set [tools] gitleaks in the configuration to its absolute path."
RAISE_THE_TIMEOUT = "For a large repository, raise [tools] gitleaks_timeout_seconds in the configuration."
ESCAPE_SEQUENCES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|[^\x20-\x7e]")
MAX_EXPLANATION = 300


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    line: int


class SecretScanner:
    def __init__(self, tools: ToolsSettings) -> None:
        self._executable = tools.gitleaks
        self._timeout_seconds = tools.gitleaks_timeout_seconds

    def scan_directory(self, root: Path) -> list[Finding]:
        """Every finding under `root`, with paths relative to it."""
        if any(on_disk_key(child.name) == ".gitleaksignore" for child in root.iterdir()):
            raise CodetrailError(f"{root} holds a .gitleaksignore, which gitleaks would obey; refusing to scan it.")
        findings = self._run(["dir", str(root)], None)
        resolved = root.resolve()
        return [
            Finding(path=str(Path(item.path).resolve().relative_to(resolved)), rule=item.rule, line=item.line)
            for item in findings
        ]

    def scan_text(self, text: str) -> list[Finding]:
        """Every finding in the text, with an empty path and the line within the text."""
        return self._run(["stdin"], text.encode("utf-8", "surrogateescape"))

    def _run(self, arguments: list[str], input: bytes | None) -> list[Finding]:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GITLEAKS_")}
        executable = self._binary(environment)
        with as_file(files("codetrail.repo") / "gitleaks.toml") as config, tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            command = [executable, *arguments, "--config", str(config), *COMMON_FLAGS, "--report-path", str(report)]
            # An empty working directory: `gitleaks stdin` would load ./.gitleaksignore from wherever it runs.
            try:
                result = subprocess.run(  # noqa: S603
                    command, input=input, capture_output=True, env=environment, check=False, cwd=folder,
                    timeout=self._timeout_seconds,
                )  # fmt: skip
            except subprocess.TimeoutExpired:
                raise CodetrailError(
                    f"gitleaks didn't finish within {self._timeout_seconds:g} seconds "
                    f"([tools] gitleaks_timeout_seconds); the scan didn't complete. {RAISE_THE_TIMEOUT}"
                ) from None
            if result.returncode != 0 or not report.exists():
                raise CodetrailError(
                    f"gitleaks failed (exit code {result.returncode}); the scan didn't complete. "
                    f"It said: {_explanation(result.stderr)}. {POINT_AT_ANOTHER}"
                )
            items = json.loads(report.read_text() or "[]")
        return [Finding(path=item["File"], rule=item["RuleID"], line=int(item["StartLine"])) for item in items]

    def _binary(self, environment: dict[str, str]) -> str:
        """The gitleaks to run; for a mise shim, the one mise picks in the folder Codetrail was started from."""
        found = shutil.which(self._executable)
        if found is None:
            raise CodetrailError(
                f"gitleaks wasn't found ({self._executable!r}); Codetrail won't read a repository without it. "
                f"Install it (for example with `mise install`). {POINT_AT_ANOTHER}"
            )
        shim = Path(found)
        mise = shim.resolve()
        if mise.name != "mise":
            return found
        # A shim picks the version from its working directory, and gitleaks runs in an empty one, so ask mise here.
        try:
            result = subprocess.run(  # noqa: S603
                [str(mise), "which", shim.name],
                stdin=subprocess.DEVNULL, capture_output=True, env=environment, check=False,
                timeout=self._timeout_seconds,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise CodetrailError(
                f"{found} is a mise shim, and mise didn't say which gitleaks it runs here within "
                f"{self._timeout_seconds:g} seconds ([tools] gitleaks_timeout_seconds); the scan didn't run. "
                f"Check the mise configuration in the folder Codetrail was started from. {POINT_AT_ANOTHER}"
            ) from None
        binary = Path(result.stdout.decode("utf-8", "replace").strip())
        if result.returncode != 0 or not binary.is_absolute():
            # Only mise's own error lines: it quotes the configuration line it couldn't parse on the lines after.
            raise CodetrailError(
                f"{found} is a mise shim, and mise couldn't say which gitleaks it runs here "
                f"(exit code {result.returncode}). It said: {_explanation(result.stderr, 'mise ERROR')}. "
                "Run Codetrail from a folder whose mise configuration sets gitleaks, or set a global version with "
                f"`mise use -g gitleaks`. {POINT_AT_ANOTHER}"
            )
        # A trusted mise configuration in the folder, perhaps a target's, can name any program with a path: version.
        installs = (shim.parent.parent / "installs" / shim.name).resolve()
        resolved = binary.resolve()  # checked and run as one path, so no link can change in between
        if not resolved.is_relative_to(installs) or not resolved.is_file():
            raise CodetrailError(
                f"mise named {_explanation(result.stdout)} as gitleaks here, which isn't one of mise's own installs "
                f"in {installs}; Codetrail won't run it. {POINT_AT_ANOTHER}"
            )
        return str(resolved)


def _explanation(stderr: bytes, prefix: str = "") -> str:
    """A program's error lines that start with `prefix`, as one printable line without colour codes, cut short."""
    lines = (ESCAPE_SEQUENCES.sub("", line).strip() for line in stderr.decode("utf-8", "replace").splitlines())
    return "; ".join(line for line in lines if line.startswith(prefix) and line)[:MAX_EXPLANATION] or "nothing"
