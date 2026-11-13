"""Scans for secrets with gitleaks (design section 3.4; ADR 0002).

gitleaks always runs with Codetrail's own configuration, ignores `gitleaks:allow` comments and `.gitleaksignore`
files, and never sees GITLEAKS_* settings, so nothing in a target can switch the scan off. Findings carry the path,
the rule and the line, never the value. A missing, failing or stuck gitleaks raises, so callers fail closed, with what
it printed. A mise shim is resolved to its binary first, since gitleaks runs in an empty folder where no version is set.

gitleaks' `dir` mode skips files and whole folders whose paths are on its default allowlist (lock files,
`node_modules/`, images, any path holding `gitleaks.toml`, ...), and any file whose first bytes look binary. A
directory scan therefore also reads every file through `stdin`, in batches that start with one long line of text,
so no batch looks binary and no path is skipped; `dir` mode still runs for the rules that depend on a path. `stdin`
is a regular file in a private temporary folder, never a pipe: gitleaks 8.30.1 (sources/file.go) checks every read
for a binary signature until a read has held a line's end, and a short read from a pipe could end inside the header
line and let the next read begin where the text itself puts a signature (tar's, at offset 257).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

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
BATCH_BYTES = 32 * 1024 * 1024  # what one `stdin` run reads at most, unless one file is larger
# gitleaks takes a whole `stdin` text for a binary file, and skips it, when its first bytes carry a binary
# signature; one line of text longer than any signature's offset opens every batch.
BATCH_HEADER = b"#" * 65535 + b"\n"


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
        resolved = root.resolve()
        findings = [
            Finding(path=str(Path(item.path).resolve().relative_to(resolved)), rule=item.rule, line=item.line)
            for item in self._run(["dir", str(root)], None)
        ]
        return list(dict.fromkeys(findings + self._scan_every_file(root)))

    def _scan_every_file(self, root: Path) -> list[Finding]:
        """Findings in every file under `root`, read through `stdin` in batches."""
        paths = sorted(
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and not path.is_symlink()
        )
        findings: list[Finding] = []
        batch: list[tuple[str, bytes]] = []
        size = 0
        for relative in paths:
            content = (root / relative).read_bytes()
            if batch and size + len(content) > BATCH_BYTES:
                findings += self._scan_batch(batch)
                batch, size = [], 0
            batch.append((relative, content))
            size += len(content)
        if batch:
            findings += self._scan_batch(batch)
        return findings

    def _scan_batch(self, batch: list[tuple[str, bytes]]) -> list[Finding]:
        """Scans the files as one text, each starting on a new line, and maps each finding back to its files' lines."""
        spans: list[tuple[int, int, str]] = []  # (the text's first and last line of a file, the file)
        pieces: list[bytes] = [BATCH_HEADER]
        line = 2  # after the header's one line
        for path, content in batch:
            if not content:
                continue  # an empty file holds nothing and takes no line
            if not content.endswith(b"\n"):
                content += b"\n"
            lines = content.count(b"\n")
            spans.append((line, line + lines - 1, path))
            pieces.append(content)
            line += lines
        # A multi-line rule can match from one file into the next, so every file the match covers is flagged.
        findings: list[Finding] = []
        for item in self._report(["stdin"], b"".join(pieces)):
            first = int(item["StartLine"])
            last = max(first, int(item["EndLine"]))
            for start, end, path in spans:
                if start <= last and first <= end:
                    findings.append(Finding(path=path, rule=str(item["RuleID"]), line=max(first, start) - start + 1))
        return findings

    def scan_text(self, text: str) -> list[Finding]:
        """Every finding in the text, with an empty path and the line within the text.

        The text goes after the batches' header line too, so one that starts like a binary file ("SQLite: …",
        "MZ-123 …") is still scanned.
        """
        return [
            Finding(path="", rule=str(item["RuleID"]), line=int(item["StartLine"]) - 1)
            for item in self._report(["stdin"], BATCH_HEADER + text.encode("utf-8", "surrogateescape"))
        ]

    def flagged_texts(self, texts: Sequence[str]) -> dict[int, str]:
        """The indexes of the texts holding a finding, each with its first finding's rule, from one gitleaks run."""
        if not texts:
            return {}
        first_lines, line = [], 1
        for text in texts:
            first_lines.append(line)
            line += text.count("\n") + 1
        flagged: dict[int, str] = {}
        for finding in self.scan_text("\n".join(texts)):
            index = max(i for i, first in enumerate(first_lines) if first <= finding.line)
            flagged.setdefault(index, finding.rule)
        return flagged

    def _run(self, arguments: list[str], input: bytes | None) -> list[Finding]:
        return [
            Finding(path=item["File"], rule=item["RuleID"], line=int(item["StartLine"]))
            for item in self._report(arguments, input)
        ]

    def _report(self, arguments: list[str], input: bytes | None) -> list[dict[str, Any]]:
        """gitleaks' report items for one run."""
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GITLEAKS_")}
        executable = self._binary(environment)
        with as_file(files("codetrail.repo") / "gitleaks.toml") as config, tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / "report.json"
            command = [executable, *arguments, "--config", str(config), *COMMON_FLAGS, "--report-path", str(report)]
            # gitleaks checks each read for a binary signature until it has seen a line's end, and a pipe can return
            # less than the header's line; a regular file returns its first 100 KB whole. The folder is private.
            text = Path(folder) / "input.txt"
            text.write_bytes(input or b"")  # empty for `dir`, which reads no `stdin`
            # An empty working directory: `gitleaks stdin` would load ./.gitleaksignore from wherever it runs.
            try:
                with text.open("rb") as stdin:
                    result = subprocess.run(  # noqa: S603
                        command, stdin=stdin, capture_output=True, env=environment, check=False, cwd=folder,
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
            items: list[dict[str, Any]] = json.loads(report.read_text() or "[]")
        return items

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
        # Its own process group, killed whole on a timeout or an interrupt (the terminal's Ctrl-C no longer reaches
        # it): a template's exec() can start commands that outlive mise.
        with subprocess.Popen(  # noqa: S603
            [str(mise), "which", shim.name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            start_new_session=True,
        ) as process:  # fmt: skip
            try:
                stdout, stderr = process.communicate(timeout=self._timeout_seconds)
            except BaseException as error:
                with suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                if not isinstance(error, subprocess.TimeoutExpired):
                    raise
                raise CodetrailError(
                    f"{found} is a mise shim, and mise didn't say which gitleaks it runs here within "
                    f"{self._timeout_seconds:g} seconds ([tools] gitleaks_timeout_seconds); the scan didn't run. "
                    f"Check the mise configuration in the folder Codetrail was started from. {POINT_AT_ANOTHER}"
                ) from None
        binary = Path(stdout.decode("utf-8", "replace").strip())
        if process.returncode != 0 or not binary.is_absolute():
            # Only mise's own error lines: it quotes the configuration line it couldn't parse on the lines after.
            raise CodetrailError(
                f"{found} is a mise shim, and mise couldn't say which gitleaks it runs here "
                f"(exit code {process.returncode}). It said: {_explanation(stderr, 'mise ERROR')}. "
                "Run Codetrail from a folder whose mise configuration sets gitleaks, or set a global version with "
                f"`mise use -g gitleaks`. {POINT_AT_ANOTHER}"
            )
        # A trusted mise configuration in the folder, perhaps a target's, can name any program with a path: version.
        installs = (shim.parent.parent / "installs" / shim.name).resolve()
        resolved = binary.resolve()  # checked and run as one path, so no link can change in between
        if not resolved.is_relative_to(installs) or not resolved.is_file():
            raise CodetrailError(
                f"mise named {_explanation(stdout)} as gitleaks here, which isn't one of mise's own installs "
                f"in {installs}; Codetrail won't run it. {POINT_AT_ANOTHER}"
            )
        return str(resolved)


def _explanation(stderr: bytes, prefix: str = "") -> str:
    """A program's error lines that start with `prefix`, as one printable line without colour codes, cut short."""
    lines = (ESCAPE_SEQUENCES.sub("", line).strip() for line in stderr.decode("utf-8", "replace").splitlines())
    return "; ".join(line for line in lines if line.startswith(prefix) and line)[:MAX_EXPLANATION] or "nothing"
