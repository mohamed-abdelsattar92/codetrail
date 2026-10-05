"""The tool guard as Claude Code's PreToolUse hook: `python -m codetrail.assistant.guard_hook <source> <read-log>`.

Claude Code runs it before every tool call, with the call as JSON on stdin. A refusal is printed as the hook's deny
decision; each allowed Read's path is appended to the read log, which becomes the page's `files`. Claude Code runs a
tool when a hook fails in any way other than exit code 2, so every error of the hook's own exits with code 2, which
refuses the call (design section 15.1). Claude Code's permission rules confine reads independently of this hook.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path


def main(arguments: list[str]) -> int:
    from codetrail.assistant.guard import ToolGuard

    source, log = Path(arguments[0]), Path(arguments[1])
    payload = json.loads(sys.stdin.read())
    guard = ToolGuard(source)
    if not isinstance(payload, Mapping):
        raise ValueError("The hook's input isn't a JSON object.")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        raise ValueError("The tool's input isn't a JSON object.")
    reason = guard.decide(str(payload.get("tool_name", "")), tool_input)
    if reason is not None:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
        return 0
    if guard.files_read:
        with log.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{path}\n" for path in guard.files_read)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except BaseException as error:  # any failure must refuse the call, never let it through
        if isinstance(error, SystemExit) and error.code == 0:
            raise
        print(
            f"Codetrail's tool guard couldn't check this call ({type(error).__name__}), so it is refused.",
            file=sys.stderr,
        )
        sys.exit(2)
