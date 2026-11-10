"""An update of a remote target records facts and writes the guide, and secrets never reach source/ (design 24.4)."""

from pathlib import Path

import pytest

from codetrail.assistant import PageDraft, PageRequest, PlanDraft
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.database import connect
from codetrail.facts import EntityKind
from codetrail.facts.store import FactStore
from codetrail.guide import GuideRepository
from codetrail.update import run_update
from tests.fixtures.remote import allow_http_remotes, serving_repository, write_served_target
from tests.fixtures.repos import Commit, fake_github_token, make_repository

TOKEN = fake_github_token()
FILES: Commit = {
    "services/api/pyproject.toml": '[project]\nname = "api"\ndependencies = ["fastapi==0.1"]\n',
    "services/api/app/__init__.py": "",
    "services/api/app/main.py": "from fastapi import FastAPI\n",
    "services/api/app/settings.py": f'TOKEN = "{TOKEN}"\n',
    ".env": "PASSWORD=not-a-real-one\n",
    "infra/prod.tfvars": "password = 1\n",
    "keys/server.pem": "not a real key\n",
}
PLAN = PlanDraft(
    [
        {"id": "areas/api", "kind": "area", "title": "The API", "scope_paths": ["services/api"], "scope_kinds": [],
         "facts": ["project:services/api"]},
    ]
)  # fmt: skip


CHECKS = [
    {
        "id": "q1",
        "question": "What is the API?",
        "rubric": [{"point": "A service", "grounds": ["project:services/api"]}],
    }
]


def write_page(request: PageRequest) -> PageDraft:
    return PageDraft(f"# {request.title}\n\nIt uses [[project:services/api]].\n", CHECKS, [], 0.01, brief="The API.")


def test_a_remote_target_updates_with_the_fake_assistant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A server on 127.0.0.1 has no service host and speaks plain http, so the test widens both, as only code can.
    allow_http_remotes(monkeypatch, tmp_path)
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    remote = make_repository(tmp_path / "remote", [FILES])
    with serving_repository(remote, tmp_path / "served") as url:
        write_served_target(paths, "shop", url)
        result = run_update(paths, "shop", claude=FakeAssistant(plans=[PLAN], page_writer=write_page))
    assert result.generation is not None
    assert result.generation.written == ["areas/api"], result.generation
    connection = connect(paths.target_data("shop") / "codetrail.db")
    assert any(entity.id == "project:services/api" for entity in FactStore(connection).entities(EntityKind.PROJECT))
    connection.close()
    assert GuideRepository(paths.target_data("shop") / "guide").read_page("areas/api") is not None
    on_disk = sorted(
        path.relative_to(paths.target_data("shop") / "source").as_posix()
        for path in (paths.target_data("shop") / "source").rglob("*")
        if path.is_file()
    )
    assert on_disk == [
        "services/api/app/__init__.py",
        "services/api/app/main.py",
        "services/api/pyproject.toml",
    ]
    assert all(
        TOKEN not in path.read_text() for path in (paths.target_data("shop") / "source").rglob("*") if path.is_file()
    )
