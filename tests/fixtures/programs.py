"""Fake provider programs for adapter tests: they record how they were run and replay a scripted output stream.

A fake is an executable script with its script baked in, because the adapters give programs an allowlisted
environment and nothing else. With `tool_calls`, a fake also runs the PreToolUse hook command from its `--settings`
file for each call, as Claude Code would, so tests exercise the real guard hook.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT = """#!{python}
import json, os, subprocess, sys
record = {{"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd(), "stdin": sys.stdin.read(), "hooks": []}}
argv = sys.argv[1:]
if "--settings" in argv:
    with open(argv[argv.index("--settings") + 1]) as handle:
        settings = json.load(handle)
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    record["hook_timeout"] = hook.get("timeout")
    for call in {tool_calls!r}:
        result = subprocess.run(hook["command"], shell=True, input=json.dumps(call), capture_output=True, text=True)
        record["hooks"].append({{"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}})
for flag in ("--output-schema",):
    if flag in argv:
        with open(argv[argv.index(flag) + 1]) as handle:
            record["schema"] = json.load(handle)
with open({record!r}, "w") as handle:
    json.dump(record, handle)
for line in {lines!r}:
    print(line, flush=True)
sys.stderr.write({stderr!r})
sys.exit({exit_code})
"""


@dataclass(frozen=True)
class FakeProgram:
    path: Path
    record_file: Path

    @property
    def record(self) -> dict[str, Any]:
        return dict(json.loads(self.record_file.read_text()))


def make_program(
    folder: Path,
    name: str,
    events: Sequence[dict[str, Any] | str],
    exit_code: int = 0,
    tool_calls: list[dict[str, Any]] | None = None,
    stderr: str = "",
) -> FakeProgram:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    record = folder / f"{name}.record.json"
    lines = [event if isinstance(event, str) else json.dumps(event) for event in events]
    path.write_text(SCRIPT.format(python=sys.executable, tool_calls=tool_calls or [], record=str(record), lines=lines,
                                  stderr=stderr, exit_code=exit_code))  # fmt: skip
    path.chmod(0o755)
    return FakeProgram(path, record)
