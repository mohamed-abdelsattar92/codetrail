"""Diagrams are drawn from facts only, escaped, and rolled up above a node limit (design section 4.4)."""

import re
from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore
from codetrail.web.diagrams import dependencies_diagram, escape_label, imports_diagram


def module(path: str) -> Entity:
    name = path.removesuffix(".py").replace("/", ".")
    return Entity(f"module:{path}", EntityKind.MODULE, {"name": name, "project": "project:."}, (Source(path),))


def imports(source: str, target: str) -> Relation:
    return Relation(f"module:{source}", RelationKind.IMPORTS, f"module:{target}")


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    return FactStore(connect(tmp_path / "codetrail.db"))


def test_a_small_scope_shows_every_module_and_edge(store: FactStore) -> None:
    store.record("c", [module("app/a.py"), module("app/b.py"), module("other/c.py")], [imports("app/a.py", "app/b.py")])
    diagram = imports_diagram(store, "app", max_nodes=10)
    assert not diagram.rolled_up
    assert sorted(node.label for node in diagram.nodes) == ["app.a", "app.b"]
    assert diagram.mermaid.startswith("flowchart LR\n")
    assert diagram.mermaid.count("-->") == 1
    assert [node.link for node in diagram.nodes if node.label == "app.a"] == ["/facts/module:app/a.py"]


def test_a_large_scope_rolls_up_to_folders_with_counts(store: FactStore) -> None:
    modules = [module(f"app/{folder}/m{index}.py") for folder in ("x", "y", "z") for index in range(5)]
    relations = [imports(f"app/x/m{index}.py", f"app/y/m{index}.py") for index in range(5)]
    relations.append(imports("app/y/m0.py", "app/z/m0.py"))
    store.record("c", modules, relations)
    diagram = imports_diagram(store, "app", max_nodes=4)
    assert diagram.rolled_up
    assert len(diagram.nodes) <= 4
    assert sorted(node.label for node in diagram.nodes) == ["app/x/", "app/y/", "app/z/"]
    assert '-->|"5"|' in diagram.mermaid
    assert diagram.mermaid.count("-->") == 2


def test_hostile_names_are_escaped(store: FactStore) -> None:
    hostile = 'app/x"]; click n1 call alert(1) %%{init}%% <script>`#.py'
    store.record("c", [module(hostile), module("app/b.py")], [imports(hostile, "app/b.py")])
    diagram = imports_diagram(store, "app", max_nodes=10)
    [line] = [line for line in diagram.mermaid.splitlines() if "alert" in line]
    label = line.split('["', 1)[1].removesuffix('"]')
    for forbidden in ('"', "]", "<", "%", "`", ";", "{"):
        assert forbidden not in re.sub(r"#(\d+|quot|lt|gt);", "", label)
    assert len(diagram.mermaid.splitlines()) == 4  # the header, two nodes and one edge: nothing injected


@pytest.mark.parametrize("text", ['a"b', "a<b>", "a#b", "a%b", "a`b", "a\nb", "a[b]", "a{b}", "a|b", "a;b"])
def test_labels_carry_no_mermaid_syntax(text: str) -> None:
    escaped = re.sub(r"#(\d+|quot|lt|gt);", "", escape_label(text))
    for character in '"<>%`\n[]{}|;':
        assert character not in escaped


def test_a_projects_dependencies(store: FactStore) -> None:
    project = Entity("project:services/api", EntityKind.PROJECT, {"name": "api"})
    packages = [Entity(f"package:pypi/{name}", EntityKind.PACKAGE) for name in ("fastapi", "httpx")]
    relations = [
        Relation(project.id, RelationKind.DEPENDS_ON, "package:pypi/fastapi", {"group": "main"}),
        Relation(project.id, RelationKind.DEPENDS_ON, "package:pypi/httpx", {"group": "dev"}),
    ]
    store.record("c", [project, *packages], relations)
    diagram = dependencies_diagram(store, project.id)
    assert sorted(node.label for node in diagram.nodes) == ["api", "fastapi", "httpx"]
    assert '-->|"dev"|' in diagram.mermaid
