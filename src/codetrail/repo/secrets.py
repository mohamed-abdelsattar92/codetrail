"""Scans for secrets with gitleaks (design section 3.4; ADR 0002).

gitleaks always runs with Codetrail's own configuration, ignores `gitleaks:allow` comments and `.gitleaksignore`
files, and never sees GITLEAKS_* settings, so nothing in a target can switch the scan off. Findings carry the path,
the rule and the line, never the value. A missing or failing gitleaks raises, so callers fail closed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from codetrail.errors import CodetrailError
from codetrail.repo.rules import on_disk_key

COMMON_FLAGS = (
    "--report-format", "json", "--redact", "--no-banner", "--exit-code", "0", "--ignore-gitleaks-allow",
    "--gitleaks-ignore-path", os.devnull, "--log-level", "error",
)  # fmt: skip


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    line: int


class SecretScanner:
    def __init__(self, executable: str) -> None:
        self._executable = executable

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
        executable = shutil.which(self._executable)
        if executable is None:
            raise CodetrailError(
                f"gitleaks wasn't found ({self._executable!r}); Codetrail won't read a repository without it. "
                "Install it (for example with `mise install`) or set [tools] gitleaks in the configuration."
            )
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GITLEAKS_")}
        with as_file(files("codetrail.repo") / "gitleaks.toml") as config, tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            command = [executable, *arguments, "--config", str(config), *COMMON_FLAGS, "--report-path", str(report)]
            # An empty working directory: `gitleaks stdin` would load ./.gitleaksignore from wherever it runs.
            result = subprocess.run(  # noqa: S603
                command, input=input, capture_output=True, env=environment, check=False, cwd=folder
            )
            if result.returncode != 0 or not report.exists():
                raise CodetrailError(f"gitleaks failed (exit code {result.returncode}); the scan didn't complete.")
            items = json.loads(report.read_text() or "[]")
        return [Finding(path=item["File"], rule=item["RuleID"], line=int(item["StartLine"])) for item in items]
