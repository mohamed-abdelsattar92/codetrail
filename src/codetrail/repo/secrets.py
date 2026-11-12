"""Scans for secrets with gitleaks (design section 3.4; ADR 0002).

gitleaks always runs with Codetrail's own configuration, ignores `gitleaks:allow` comments and `.gitleaksignore`
files, and never sees GITLEAKS_* settings, so nothing in a target can switch the scan off. Findings carry the path,
the rule and the line, never the value. A missing, failing or stuck gitleaks raises, so callers fail closed, with what
it printed. A mise shim is resolved to its binary first, since gitleaks runs in an empty folder where no version is set.

gitleaks' default configuration, which Codetrail's extends, skips a list of paths in `dir` mode (lock files,
`node_modules/`, images, any path holding `gitleaks.toml`, ...). A directory scan therefore also reads the files on
that list through `stdin`, where no path is skipped, so every file under the folder is scanned.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
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
# The global path allowlist of gitleaks 8.30.1's default configuration (`[allowlist] paths`, read from the binary
# pinned in .mise.toml). Update it with the pin.
SKIPPED_BY_GITLEAKS = re.compile(
    "|".join(
        f"(?:{pattern})"
        for pattern in (
            r"gitleaks\.toml",
            r"(?i:\.(?:bmp|gif|jpe?g|png|svg|tiff?)$)",
            r"(?i:\.(?:eot|[ot]tf|woff2?)$)",
            r"(?i:\.(?:docx?|xlsx?|pdf|bin|socket|vsidx|v2|suo|wsuo|.dll|pdb|exe|gltf)$)",
            r"go\.(?:mod|sum|work(?:\.sum)?)$",
            r"(?:^|/)vendor/modules\.txt$",
            r"(?:^|/)vendor/(?:github\.com|golang\.org/x|google\.golang\.org|gopkg\.in|istio\.io|k8s\.io|sigs\.k8s\.io)"
            r"(?:/.*)?$",
            r"(?:^|/)gradlew(?:\.bat)?$",
            r"(?:^|/)gradle\.lockfile$",
            r"(?:^|/)mvnw(?:\.cmd)?$",
            r"(?:^|/)\.mvn/wrapper/MavenWrapperDownloader\.java$",
            r"(?:^|/)node_modules(?:/.*)?$",
            r"(?:^|/)(?:deno\.lock|npm-shrinkwrap\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$",
            r"(?:^|/)bower_components(?:/.*)?$",
            r"(?:^|/)(?:angular|bootstrap|jquery(?:-?ui)?|plotly|swagger-?ui)[a-zA-Z0-9.-]*(?:\.min)?\.js(?:\.map)?$",
            r"(?:^|/)javascript\.json$",
            r"(?:^|/)(?:Pipfile|poetry)\.lock$",
            r"(?i:(?:^|/)(?:v?env|virtualenv)/lib(?:64)?(?:/.*)?$)",
            r"(?i:(?:^|/)(?:lib(?:64)?/python[23](?:\.\d{1,2})+|python/[23](?:\.\d{1,2})+/lib(?:64)?)(?:/.*)?$)",
            r"(?i:(?:^|/)[a-z0-9_.]+-[0-9.]+\.dist-info(?:/.+)?$)",
            r"(?:^|/)vendor/(?:bundle|ruby)(?:/.*?)?$",
            r"\.gem$",
            r"verification-metadata\.xml",
            r"Database.refactorlog",
        )
    )
)
SKIPPED_BATCH_BYTES = 32 * 1024 * 1024  # what one `stdin` run of skipped files reads at most, unless one file is larger


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
        ] + self._scan_skipped(root)

    def _scan_skipped(self, root: Path) -> list[Finding]:
        """Findings in the files gitleaks' `dir` mode skips, read through `stdin` in batches."""
        skipped = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and SKIPPED_BY_GITLEAKS.search(path.relative_to(root).as_posix())
        )
        findings: list[Finding] = []
        batch: list[tuple[str, bytes]] = []
        size = 0
        for path in skipped:
            content = (root / path).read_bytes()
            if batch and size + len(content) > SKIPPED_BATCH_BYTES:
                findings += self._scan_batch(batch)
                batch, size = [], 0
            batch.append((path, content))
            size += len(content)
        if batch:
            findings += self._scan_batch(batch)
        return findings

    def _scan_batch(self, batch: list[tuple[str, bytes]]) -> list[Finding]:
        """Scans the files as one text, each starting on a new line, and maps each finding back to its files' lines."""
        spans: list[tuple[int, int, str]] = []  # (the text's first and last line of a file, the file)
        pieces: list[bytes] = []
        line = 1
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
        """Every finding in the text, with an empty path and the line within the text."""
        return self._run(["stdin"], text.encode("utf-8", "surrogateescape"))

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
