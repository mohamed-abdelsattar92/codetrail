"""User MCP programs must not run inside Codetrail's Codex calls; no sign-in or model service is needed."""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from codetrail.assistant import AssistantError, QuestionRequest, codex
from codetrail.config import CodexSettings, GenerationSettings

pytestmark = pytest.mark.live


@pytest.mark.anyio
async def test_codex_does_not_start_user_mcp_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    program = shutil.which("codex")
    if program is None:
        pytest.skip("Codex isn't installed here.")
    home = tmp_path / "home"
    home.mkdir()
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    marker = tmp_path / "user-mcp-started"
    arguments = ["-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    (codex_home / "config.toml").write_text(
        f"[mcp_servers.unrelated]\ncommand = {json.dumps(sys.executable)}\n"
        f"args = {json.dumps(arguments)}\nrequired = true\n"
    )
    # Force any model request to fail locally: this checks process isolation, not model output.
    monkeypatch.setattr(
        codex,
        "ISOLATION",
        (
            *codex.ISOLATION,
            'model_provider="isolation-probe"',
            'model_providers.isolation-probe={name="Isolation probe", base_url="http://127.0.0.1:1/v1", '
            'wire_api="responses", request_max_retries=0, stream_max_retries=0}',
        ),
    )
    assistant = codex.CodexAssistant(
        source,
        {},
        CodexSettings(command=program, timeout_seconds=20),
        GenerationSettings(),
        retries=0,
        environ={"PATH": os.environ["PATH"], "HOME": str(home), "CODEX_HOME": str(codex_home)},
    )
    with pytest.raises(AssistantError):
        async for _chunk in assistant.answer(QuestionRequest("tiny", "Reply OK without tools.", "en")):
            pass
    assert not marker.exists(), "Codetrail started an unrelated program from the user's MCP configuration"
