"""The real Agent SDK adapter against the real Claude, on a tiny fixture (`just test-live`; CI skips it)."""

from pathlib import Path

import anyio
import pytest

from codetrail.claude import PageRequest, PlanRequest
from codetrail.claude.agent_sdk import AgentSdkClaude
from codetrail.config import GenerationSettings, ModelSettings

pytestmark = pytest.mark.live
CHEAP = "claude-haiku-4-5-20251001"


@pytest.fixture
def adapter(tmp_path: Path) -> AgentSdkClaude:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("from app import db\n\nprint(db.VALUE)\n")
    (tmp_path / "app" / "db.py").write_text("VALUE = 42  # the answer\n")
    (tmp_path / "README.md").write_text("# Tiny\n\nA tiny app that prints a value from its db module.\n")
    models = ModelSettings(plan=CHEAP, write=CHEAP, digest=CHEAP, answer=CHEAP)
    return AgentSdkClaude(tmp_path, models, GenerationSettings(max_turns=8, max_budget_usd_per_call=0.5))


def test_plan_returns_pages(adapter: AgentSdkClaude) -> None:
    draft = anyio.run(adapter.plan, PlanRequest("tiny", "module:app/main.py\nmodule:app/db.py", ""))
    assert draft.pages
    assert all("id" in page for page in draft.pages)


def test_write_page_reads_only_inside_and_returns_a_body(adapter: AgentSdkClaude) -> None:
    request = PageRequest("areas/app", "area", "The app", ["app"], "module module:app/db.py {}", "", "")
    draft = anyio.run(adapter.write_page, request)
    assert draft.body.strip()
    assert all(not path.startswith("/") and ".." not in path for path in draft.files_read)
