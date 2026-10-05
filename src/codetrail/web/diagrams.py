"""Diagrams drawn from facts only, as Mermaid text (design section 4.4).

Every node and edge is a fact, so a diagram can't show a connection the code doesn't have. Node ids are generated and
labels are escaped to Mermaid's entity codes, so a name from the target can't inject Mermaid syntax. Above the node
limit, modules roll up to their folders, one level at a time, with the number of imports on each edge.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codetrail.facts import EntityKind, RelationKind
from codetrail.facts.store import FactStore

# Mermaid reads #<code>; as an entity inside a quoted label; anything that could close the label, start a link, a
# directive or a new statement is replaced.
ESCAPES = {
    "#": "#35;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "%": "#37;", "`": "#96;", "[": "#91;", "]": "#93;",
    "{": "#123;", "}": "#125;", "|": "#124;", ";": "#59;", "\\": "#92;", "&": "#38;",
}  # fmt: skip


def escape_label(text: str) -> str:
    flattened = " ".join(text.split())
    return "".join(ESCAPES.get(character, character) for character in flattened)


@dataclass(frozen=True)
class DiagramNode:
    id: str
    label: str
    link: str


@dataclass(frozen=True)
class Diagram:
    mermaid: str
    nodes: list[DiagramNode] = field(default_factory=list)
    rolled_up: bool = False


def imports_diagram(store: FactStore, scope: str, max_nodes: int) -> Diagram:
    """The imports between modules under the folder `scope`, rolled up to folders above `max_nodes`."""
    prefix = scope.rstrip("/") + "/"
    modules = {
        entity.id: entity
        for entity in store.entities(EntityKind.MODULE)
        if entity.id.removeprefix("module:").startswith(prefix)
    }
    edges = [
        (relation.source_id, relation.target_id)
        for relation in store.relations(RelationKind.IMPORTS)
        if relation.source_id in modules and relation.target_id in modules
    ]
    if len(modules) <= max_nodes:
        group = {module_id: module_id for module_id in modules}
        labels = {module_id: str(entity.attributes.get("name", module_id)) for module_id, entity in modules.items()}
        links = {module_id: f"/facts/{module_id}" for module_id in modules}
        return _render(group, labels, links, Counter(edges), rolled_up=False, counted=False)
    paths = {module_id: PurePosixPath(module_id.removeprefix("module:")).parent for module_id in modules}
    depth = max(len(path.parts) for path in paths.values())
    while depth > 1 and len({_truncate(path, depth) for path in paths.values()}) > max_nodes:
        depth -= 1
    group = {module_id: str(_truncate(path, depth)) for module_id, path in paths.items()}
    labels = {folder: f"{folder}/" for folder in group.values()}
    links = {folder: f"/areas/{folder}" for folder in group.values()}
    folder_edges = Counter((group[source], group[target]) for source, target in edges if group[source] != group[target])
    return _render(group, labels, links, folder_edges, rolled_up=True, counted=True)


def dependencies_diagram(store: FactStore, project_id: str) -> Diagram:
    """A project and the packages it depends on, each edge labelled with its dependency group."""
    project = store.entity(project_id)
    if project is None:
        return Diagram("flowchart LR\n")
    relations = [r for r in store.relations(RelationKind.DEPENDS_ON) if r.source_id == project_id]
    labels = {project_id: str(project.attributes.get("name", project_id))}
    for relation in relations:
        labels[relation.target_id] = relation.target_id.removeprefix("package:pypi/")
    ids = {key: f"n{index}" for index, key in enumerate(sorted(labels), start=1)}
    lines = ["flowchart LR"]
    lines += [f'    {ids[key]}["{escape_label(label)}"]' for key, label in sorted(labels.items())]
    for relation in relations:
        group = escape_label(str(relation.attributes.get("group", "")))
        lines.append(f'    {ids[project_id]} -->|"{group}"| {ids[relation.target_id]}')
    nodes = [DiagramNode(ids[key], label, f"/facts/{key}") for key, label in sorted(labels.items())]
    return Diagram("\n".join(lines) + "\n", nodes)


def _truncate(path: PurePosixPath, depth: int) -> PurePosixPath:
    return PurePosixPath(*path.parts[:depth]) if path.parts else path


def _render(
    group: dict[str, str],
    labels: dict[str, str],
    links: dict[str, str],
    edges: Counter[tuple[str, str]],
    *,
    rolled_up: bool,
    counted: bool,
) -> Diagram:
    keys = sorted(set(group.values()))
    ids = {key: f"n{index}" for index, key in enumerate(keys, start=1)}
    lines = ["flowchart LR"]
    lines += [f'    {ids[key]}["{escape_label(labels[key])}"]' for key in keys]
    for (source, target), count in sorted(edges.items()):
        arrow = f'-->|"{count}"|' if counted else "-->"
        lines.append(f"    {ids[source]} {arrow} {ids[target]}")
    nodes = [DiagramNode(ids[key], labels[key], links[key]) for key in keys]
    return Diagram("\n".join(lines) + "\n", nodes, rolled_up)
