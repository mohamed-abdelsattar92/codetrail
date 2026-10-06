"""Which pages an update writes, and in what order (design section 6.2)."""

from pathlib import Path

from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.update import run_update
from tests.fixtures.repos import add_commit
from tests.integration.test_generation import ADR, PLAN, good_page
from tests.integration.test_generation import paths as paths  # the fixture


def set_generation(paths: Paths, settings: str) -> None:
    file = paths.target_file("t")
    text = file.read_text().split("[generation]")[0]
    file.write_text(text + "[generation]\n" + settings)


def test_pages_this_update_changed_come_before_older_backlog(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page))
    target = tmp_path / "target"
    add_commit(target, {"docs/adr/0001-use-fastapi.md": ADR.replace("accepted", "superseded")}, "docs(adr): supersede")
    set_generation(paths, "max_pages_per_update = 0\n")  # nothing written: the ADR's page waits
    assert run_update(paths, "t", claude=FakeAssistant()).generation.left_for_later == ["concepts/fastapi"]  # type: ignore[union-attr]
    add_commit(target, {"services/api/app/routes.py": "from app import db\n"}, "feat(api): routes")
    set_generation(paths, "max_pages_per_update = 1\n")
    generation = run_update(paths, "t", claude=FakeAssistant(page_writer=good_page)).generation
    assert generation is not None
    assert generation.written == ["areas/api"]  # its facts changed in this update
    assert generation.left_for_later == ["concepts/fastapi"]
