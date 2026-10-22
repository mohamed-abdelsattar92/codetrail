"""Every update that records facts writes the context for coding agents (design section 23.4)."""

from pathlib import Path

import pytest

from codetrail.agents.export import read_export
from codetrail.assistant import PageDraft, PageRequest
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.update import run_update
from tests.fixtures.repos import add_commit
from tests.integration.test_generation import PLAN, good_page
from tests.integration.test_generation import paths as paths  # the fixture


def agents(paths: Paths) -> Path:
    return paths.target_data("t") / "agents"


def test_an_update_writes_the_export_after_the_guide(paths: Paths) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    export = read_export(agents(paths))
    assert export is not None
    api = next(page for page in export.pages if page.id == "areas/api")
    assert "`module:services/api/app/main.py`" in api.brief  # the page's own brief, converted
    assert (agents(paths) / "map.md").read_text() == export.map_text


def test_a_facts_only_update_rebuilds_the_map(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", facts_only=True)
    commit = add_commit(tmp_path / "target", {"services/web/pyproject.toml": '[project]\nname = "web"\n'}, "feat: web")
    run_update(paths, "t", facts_only=True)
    export = read_export(agents(paths))
    assert export is not None and f"facts at {commit[:12]}" in export.map_text


def test_a_failed_update_leaves_the_old_export(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    index = agents(paths) / "index.json"
    before = index.read_bytes()
    add_commit(tmp_path / "target", {"services/api/app/routes.py": "from app import db\n"}, "feat(api): routes")

    def broken(request: PageRequest) -> PageDraft:
        raise RuntimeError("the writer broke")

    with pytest.raises(RuntimeError):
        run_update(paths, "t", claude=FakeAssistant(page_writer=broken))
    assert index.read_bytes() == before
