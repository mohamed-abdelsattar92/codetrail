"""An update runs the system pass over a mixed repository and records its parts and connections (design 17.3)."""

from pathlib import Path

import pytest

from codetrail.config import Paths, write_target
from codetrail.database import connect
from codetrail.facts import EntityKind, RelationKind
from codetrail.facts.store import FactStore
from codetrail.update import UpdateResult, run_update
from tests.fixtures.repos import Commit, make_repository

CONTRACT = """openapi: 3.1.0
info: {title: Shop, version: "1"}
paths:
  /orders:
    get:
      responses: {"200": {description: ok}}
"""
FILES: Commit = {
    "services/api/pyproject.toml": '[project]\nname = "api"\ndependencies = ["fastapi>=0.1"]\n',
    "services/api/app/main.py": "import fastapi\n",
    "services/api/Makefile": "check:\n\tvalidate ../../contracts/openapi.yaml\n",
    "contracts/openapi.yaml": CONTRACT,
    "apps/site/package.json": '{"name": "site", "dependencies": {"astro": "^5"}, "scripts": {"types": '
    '"openapi-typescript ../../contracts/openapi.yaml"}}',
    "apps/site/astro.config.mjs": "export default {};\n",
    "apps/site/wrangler.jsonc": '{"name": "shop-site", "main": "src/worker.ts", "d1_databases": [{"binding": "DB"}]}',
    "apps/site/src/worker.ts": "export default {};\n",
    "apps/site/src/pages/index.astro": "---\n---\n<p>hi</p>\n",
    "infra/app/main.tf": 'resource "google_cloud_run_v2_service" "api" {\n  name = "api"\n}\n',
    ".github/workflows/deploy.yml": "jobs:\n  site:\n    steps:\n      - run: npx wrangler deploy\n"
    "        working-directory: apps/site\n",
}


@pytest.fixture
def result(tmp_path: Path) -> tuple[Paths, UpdateResult]:
    paths = Paths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    write_target(paths, "shop", make_repository(tmp_path / "target", [FILES]), "develop")
    return paths, run_update(paths, "shop", facts_only=True)


def test_an_update_records_the_systems_parts_and_connections(result: tuple[Paths, UpdateResult]) -> None:
    paths, update = result
    connection = connect(paths.target_data("shop") / "codetrail.db")
    store = FactStore(connection)
    parts = {entity.id: entity.attributes["kind"] for entity in store.entities(EntityKind.PART)}
    assert parts["part:services/api"] == "service"
    assert parts["part:apps/site"] == "app"
    assert parts["part:contracts/openapi.yaml"] == "contract"
    assert parts["part:infra/app"] == "infrastructure"
    arrows = {(r.source_id, str(r.kind), r.target_id): r.attributes for r in store.relations()
              if r.kind in (RelationKind.IMPLEMENTS, RelationKind.CALLS_VIA, RelationKind.DEPLOYED_ON)}  # fmt: skip
    assert ("part:services/api", "implements", "part:contracts/openapi.yaml") in arrows
    assert ("part:apps/site", "calls_via", "part:contracts/openapi.yaml") in arrows
    assert arrows[("part:apps/site", "deployed_on", "part:platform:cloudflare")]["evidence"] == "explicit"
    assert arrows[("part:services/api", "deployed_on", "part:platform:google_cloud")]["evidence"] == "matched"
    assert update.extraction.system["implements"] == 1
    connection.close()


def test_the_system_reader_refuses_unlisted_and_oversized_files(tmp_path: Path) -> None:
    from codetrail.update import system_reader

    source = tmp_path / "source"
    (source / "a").mkdir(parents=True)
    (source / "a/listed.txt").write_text("ok")
    (source / "a/big.txt").write_text("x" * 50)
    (source / "a/unlisted.txt").write_text("secret")
    read = system_reader(source, {"a/listed.txt": "b1", "a/big.txt": "b2"}, max_bytes=10)
    assert read("a/listed.txt") == b"ok"
    assert read("a/big.txt") is None
    assert read("a/unlisted.txt") is None
    assert read("../outside") is None


def test_the_system_reader_refuses_symlinks_and_escapes(tmp_path: Path) -> None:
    from codetrail.update import system_reader

    source = tmp_path / "source"
    (source / "a").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (source / "a/link.txt").symlink_to(outside / "secret.txt")
    (source / "linked").symlink_to(outside)
    read = system_reader(source, {"a/link.txt": "b1", "linked/secret.txt": "b2"}, max_bytes=100)
    assert read("a/link.txt") is None  # a listed path that is a symlink
    assert read("linked/secret.txt") is None  # a listed path under a folder that leads outside source/
