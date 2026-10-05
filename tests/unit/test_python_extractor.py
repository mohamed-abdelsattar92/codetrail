"""The python extractor: projects, modules, packages and imports (design section 5.2)."""

from pathlib import Path

from codetrail.extract import run_extractors
from codetrail.extract.python import PythonExtractor
from codetrail.facts import EntityKind, RelationKind

PYPROJECT = """
[project]
name = "shop-api"
requires-python = ">=3.14"
dependencies = ["fastapi==0.120.0", "PyYAML>=6", "SQLAlchemy[asyncio]~=2.0"]

[dependency-groups]
dev = ["pytest==9.1.1"]
"""


def write(root: Path, files: dict[str, str]) -> list[str]:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return sorted(files)


def extract(root: Path, files: dict[str, str]):  # type: ignore[no-untyped-def]
    return run_extractors(root, write(root, files), [PythonExtractor()])


def test_projects_packages_and_dependencies(tmp_path: Path) -> None:
    extraction = extract(tmp_path, {"services/api/pyproject.toml": PYPROJECT})
    entities = {entity.id: entity for entity in extraction.entities}
    project = entities["project:services/api"]
    assert project.attributes == {"name": "shop-api", "requires_python": ">=3.14"}
    assert {"package:pypi/fastapi", "package:pypi/pyyaml", "package:pypi/sqlalchemy", "package:pypi/pytest"} <= set(
        entities
    )
    depends = {relation.target_id: dict(relation.attributes) for relation in extraction.relations}
    assert depends["package:pypi/fastapi"] == {"specifier": "==0.120.0", "group": "main"}
    assert depends["package:pypi/sqlalchemy"] == {"specifier": "~=2.0", "group": "main"}
    assert depends["package:pypi/pytest"] == {"specifier": "==9.1.1", "group": "dev"}


def test_modules_in_a_flat_layout_with_imports(tmp_path: Path) -> None:
    extraction = extract(
        tmp_path,
        {
            "services/api/pyproject.toml": PYPROJECT,
            "services/api/app/__init__.py": "",
            "services/api/app/db.py": "import os\nimport yaml\nfrom sqlalchemy import select\n",
            "services/api/app/routes/__init__.py": "",
            "services/api/app/routes/orders.py": "from ..db import engine\nfrom . import helpers\nimport app.db\n",
            "services/api/app/routes/helpers.py": "from fastapi import APIRouter\nimport unknown_thing\n",
        },
    )
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["module:services/api/app/routes/orders.py"].attributes == {
        "name": "app.routes.orders",
        "project": "project:services/api",
    }
    assert entities["module:services/api/app/__init__.py"].attributes["name"] == "app"
    edges = {(r.source_id, r.kind, r.target_id) for r in extraction.relations}
    orders = "module:services/api/app/routes/orders.py"
    assert (orders, RelationKind.IMPORTS, "module:services/api/app/db.py") in edges
    assert (orders, RelationKind.IMPORTS, "module:services/api/app/routes/helpers.py") in edges
    db = "module:services/api/app/db.py"
    assert (db, RelationKind.IMPORTS, "package:pypi/pyyaml") in edges
    assert (db, RelationKind.IMPORTS, "package:pypi/sqlalchemy") in edges
    assert not any(target.endswith("/os") for _, _, target in edges)
    assert ("project:services/api", RelationKind.CONTAINS, orders) in edges
    assert extraction.unresolved == {"python": 1}  # unknown_thing


def test_a_src_layout_strips_src(tmp_path: Path) -> None:
    extraction = extract(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "tool"\n',
            "src/tool/__init__.py": "",
            "src/tool/cli.py": "from tool.core import run\nfrom .core import go\n",
            "src/tool/core.py": "",
        },
    )
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["module:src/tool/cli.py"].attributes["name"] == "tool.cli"
    edges = {(r.source_id, r.target_id) for r in extraction.relations if r.kind is RelationKind.IMPORTS}
    assert ("module:src/tool/cli.py", "module:src/tool/core.py") in edges


def test_from_package_import_submodule(tmp_path: Path) -> None:
    extraction = extract(
        tmp_path,
        {
            "pyproject.toml": '[project]\nname = "x"\n',
            "pkg/__init__.py": "",
            "pkg/sub.py": "",
            "main.py": "from pkg import sub\n",
        },
    )
    edges = {(r.source_id, r.target_id) for r in extraction.relations if r.kind is RelationKind.IMPORTS}
    assert ("module:main.py", "module:pkg/sub.py") in edges


def test_a_broken_file_still_yields_its_module(tmp_path: Path) -> None:
    extraction = extract(
        tmp_path, {"pyproject.toml": '[project]\nname = "x"\n', "a.py": "import b\ndef broken(:\n", "b.py": ""}
    )
    edges = {(r.source_id, r.target_id) for r in extraction.relations if r.kind is RelationKind.IMPORTS}
    assert ("module:a.py", "module:b.py") in edges
    assert extraction.warnings == []


def test_files_outside_any_project_are_ignored(tmp_path: Path) -> None:
    extraction = extract(tmp_path, {"tools/script.py": "import os\n"})
    assert [entity for entity in extraction.entities if entity.kind is EntityKind.MODULE] == []


def test_a_broken_pyproject_is_a_warning(tmp_path: Path) -> None:
    extraction = extract(tmp_path, {"pyproject.toml": "[project\n"})
    assert extraction.warnings == ["python: pyproject.toml: could not be read (TOMLDecodeError)"]


def test_non_text_project_values_are_ignored(tmp_path: Path) -> None:
    extraction = extract(tmp_path, {"pyproject.toml": "[project]\nname = 2026-01-01\nrequires-python = 3\n"})
    [project] = [entity for entity in extraction.entities if entity.kind is EntityKind.PROJECT]
    assert dict(project.attributes) == {}


def test_credentials_in_requirement_urls_are_never_stored(tmp_path: Path) -> None:
    import json

    toml = '[project]\nname = "x"\ndependencies = ["lib @ git+https://user:s3cret@git.example/org/lib.git"]\n'
    found = run_extractors(tmp_path, write(tmp_path, {"pyproject.toml": toml}), [PythonExtractor()])
    stored = json.dumps([dict(relation.attributes) for relation in found.relations])
    assert "s3cret" not in stored and "git.example/org/lib.git" in stored
