"""Diagrams drawn from facts only, as Mermaid text (design section 4.4).

Every node and edge is a fact, so a diagram can't show a connection the code doesn't have. Node ids are generated and
labels are escaped to Mermaid's entity codes, so a name from the target can't inject Mermaid syntax. Above the node
limit, modules roll up to their folders, one level at a time, with the number of imports on each edge.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codetrail.facts import Entity, EntityKind, RelationKind
from codetrail.facts.store import FactStore

# Mermaid reads #<code>; as an entity inside a quoted label; anything that could close the label, start a link, a
# directive or a new statement is replaced.
ESCAPES = {
    "#": "#35;", '"': "#quot;", "<": "#lt;", ">": "#gt;", "%": "#37;", "`": "#96;", "[": "#91;", "]": "#93;",
    "{": "#123;", "}": "#125;", "|": "#124;", ";": "#59;", "\\": "#92;", "&": "#38;",
}  # fmt: skip


def escape_label(text: str, fallback: str = "?") -> str:
    """A label Mermaid reads as plain text; an empty one (Mermaid refuses `[""]`) becomes the fallback."""
    flattened = " ".join(text.split()) or " ".join(fallback.split()) or "?"
    return "".join(ESCAPES.get(character, character) for character in flattened)


def _node(node_id: str, label: str, key: str) -> str:
    return f'    {node_id}["{escape_label(label, fallback=key)}"]'


def _edge(source: str, target: str, label: str) -> str:
    """An arrow, labelled only when there's a label: Mermaid refuses an empty one (`-->|""|`)."""
    text = " ".join(label.split())
    return f'    {source} -->|"{escape_label(text)}"| {target}' if text else f"    {source} --> {target}"


@dataclass(frozen=True)
class DiagramNode:
    id: str
    label: str
    link: str


@dataclass(frozen=True)
class DiagramArrow:
    """One connection in the system diagram and why it's there: its source file and line, or the rule's two names."""

    source: str
    kind: str
    target: str
    evidence: str
    link: str | None
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Diagram:
    mermaid: str
    nodes: list[DiagramNode] = field(default_factory=list)
    rolled_up: bool = False
    arrows: list[DiagramArrow] = field(default_factory=list)


def imports_diagram(store: FactStore, scope: str, max_nodes: int) -> Diagram:
    """The imports between modules under the folder `scope`, rolled up to folders above `max_nodes`."""
    prefix = scope.rstrip("/") + "/"
    modules = {
        entity.id: entity
        for kind in (EntityKind.MODULE, EntityKind.SWIFT_TARGET)
        for entity in store.entities(kind)
        if _location(entity.id).startswith(prefix)
    }
    edges = [
        (relation.source_id, relation.target_id)
        for kind in (RelationKind.IMPORTS, RelationKind.DEPENDS_ON)
        for relation in store.relations(kind)
        if relation.source_id in modules and relation.target_id in modules
    ]
    if len(modules) <= max_nodes:
        group = {module_id: module_id for module_id in modules}
        labels = {module_id: str(entity.attributes.get("name", module_id)) for module_id, entity in modules.items()}
        links = {module_id: f"/facts/{module_id}" for module_id in modules}
        return _render(group, labels, links, Counter(edges), rolled_up=False, counted=False)
    paths = {module_id: PurePosixPath(_location(module_id)).parent for module_id in modules}
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
        labels[relation.target_id] = _package_name(relation.target_id)
    ids = {key: f"n{index}" for index, key in enumerate(sorted(labels), start=1)}
    lines = ["flowchart LR"]
    lines += [_node(ids[key], label, key) for key, label in sorted(labels.items())]
    for relation in relations:
        lines.append(_edge(ids[project_id], ids[relation.target_id], str(relation.attributes.get("group") or "")))
    nodes = [DiagramNode(ids[key], label, f"/facts/{key}") for key, label in sorted(labels.items())]
    return Diagram("\n".join(lines) + "\n", nodes)


def resources_diagram(store: FactStore, scope: str, max_nodes: int) -> Diagram:
    """Terraform modules under the folder `scope`, the modules they call and, when they fit, their resources."""
    prefix = scope.rstrip("/") + "/"
    modules = {
        entity.id: entity for entity in store.entities(EntityKind.TERRAFORM_MODULE)
        if (_location(entity.id) + "/").startswith(prefix) or prefix.startswith(_location(entity.id) + "/")
    }  # fmt: skip
    resources = {
        entity.id: entity for entity in store.entities(EntityKind.RESOURCE)
        if f"terraform_module:{entity.attributes.get('module')}" in modules
    }  # fmt: skip
    nodes = dict(modules) if len(modules) + len(resources) > max_nodes else {**modules, **resources}
    labels = {key: (f"{_location(key)}/" if key in modules else _location(key).rsplit("/", 1)[-1]) for key in nodes}
    edges: Counter[tuple[str, str]] = Counter()
    for relation in store.relations(RelationKind.REFERENCES) + store.relations(RelationKind.CONTAINS):
        if relation.source_id in nodes and relation.target_id in nodes:
            edges[(relation.source_id, relation.target_id)] += 1
    return _render({key: key for key in nodes}, labels, {key: f"/facts/{key}" for key in nodes}, edges,
                   rolled_up=len(nodes) < len(modules) + len(resources), counted=False)  # fmt: skip


# Each kind of part has its own shape (Mermaid's syntax around a quoted, escaped label).
SHAPES = {
    "service": ('("', '")'),
    "app": ('("', '")'),
    "library": ('["', '"]'),
    "contract": ('@{ shape: doc, label: "', '" }'),
    "infrastructure": ('[["', '"]]'),
    "platform": ('(["', '"])'),
}
SYSTEM_RELATIONS = (RelationKind.DEPENDS_ON, RelationKind.IMPLEMENTS, RelationKind.CALLS_VIA, RelationKind.DEPLOYED_ON)
ARROW_LABELS = {RelationKind.IMPLEMENTS: "implements", RelationKind.CALLS_VIA: "calls via",
                RelationKind.DEPLOYED_ON: "deployed on"}  # fmt: skip


def system_diagram(store: FactStore, focus: str | None, max_nodes: int) -> Diagram:
    """How the repository's parts connect (design 17.4), or, with `focus`, the parts in a folder and their neighbours.

    A client that calls through a contract a service implements gets an arrow to that service, labelled with the
    contract. Matched connections are dashed. Above `max_nodes`, libraries roll up into their parent folders, then
    every part but the platforms into its top-level folder.
    """
    parts = {entity.id: entity for entity in store.entities(EntityKind.PART)}
    relations = [
        relation for kind in SYSTEM_RELATIONS for relation in store.relations(kind)
        if relation.source_id in parts and relation.target_id in parts
    ]  # fmt: skip
    implementers: dict[str, list[str]] = {}
    for relation in relations:
        if relation.kind is RelationKind.IMPLEMENTS:
            implementers.setdefault(relation.target_id, []).append(relation.source_id)
    edges: dict[tuple[str, str], tuple[str, bool]] = {}  # (source, target) -> (label, matched)
    for relation in relations:
        matched = relation.attributes.get("evidence") == "matched"
        if relation.kind is RelationKind.CALLS_VIA and relation.target_id in implementers:
            contract = str(parts[relation.target_id].attributes.get("name", ""))
            for service in implementers[relation.target_id]:
                edges.setdefault((relation.source_id, service), (contract, matched))
            continue
        label = "matched by name" if matched else ARROW_LABELS.get(relation.kind, "")
        edges.setdefault((relation.source_id, relation.target_id), (label, matched))
    shown = set(parts)
    if focus is not None:
        inside = {key for key, entity in parts.items() if _within(_part_folder(entity), focus.strip("/"))
                  and entity.attributes.get("kind") != "platform"}  # fmt: skip
        pairs = [*edges, *((relation.source_id, relation.target_id) for relation in relations)]
        shown = inside | {other for pair in pairs if set(pair) & inside for other in pair}
    edges = {pair: value for pair, value in edges.items() if set(pair) <= shown}
    relations = [relation for relation in relations if {relation.source_id, relation.target_id} <= shown]
    group = {key: key for key in shown}
    labels = {key: str(parts[key].attributes.get("name") or "") for key in shown}
    kinds = {key: str(parts[key].attributes.get("kind", "library")) for key in shown}
    links = {key: f"/facts/{key}" for key in shown}
    rolled_up = False
    for level in ("libraries", "parts"):
        if len(set(group.values())) <= max_nodes:
            break
        rolled_up = True
        members: dict[str, list[str]] = {}
        for key in shown:
            if level == "libraries" and kinds[key] == "library":
                folder = str(PurePosixPath(_part_folder(parts[key])).parent)
            elif level == "parts" and kinds[key] != "platform":
                folder = _part_folder(parts[key]).split("/", 1)[0] or "."
            else:
                continue
            members.setdefault(f"group:{folder}", []).append(key)
        for folder_key, keys in members.items():
            folder = folder_key.removeprefix("group:")
            noun = "libraries" if level == "libraries" else "parts"
            labels[folder_key] = f"{folder}/ ({len(keys)} {noun})"
            kinds[folder_key] = "library"
            links[folder_key] = f"/areas/{folder}"
            for key in keys:
                group[key] = folder_key
    counted: Counter[tuple[str, str]] = Counter()
    merged: dict[tuple[str, str], tuple[str, bool]] = {}
    for (source, target), (label, matched) in edges.items():
        pair = (group[source], group[target])
        if pair[0] == pair[1]:
            continue
        counted[pair] += 1
        before = merged.get(pair)
        if before is not None:  # merged arrows keep a label they share, and are dashed only if all were matched
            label, matched = (label if before[0] == label else ""), before[1] and matched
        merged[pair] = (label, matched)
    keys = sorted(set(group.values()))
    ids = {key: f"n{index}" for index, key in enumerate(keys, start=1)}
    lines = ["flowchart LR"]
    for key in keys:
        start, end = SHAPES.get(kinds[key], SHAPES["library"])
        lines.append(f"    {ids[key]}{start}{escape_label(labels[key], fallback=key)}{end}")
    for (source, target), (label, matched) in sorted(merged.items()):
        text = str(counted[(source, target)]) if counted[(source, target)] > 1 else label
        arrow = "-.->" if matched else "-->"
        lines.append(f'    {ids[source]} {arrow}|"{escape_label(text)}"| {ids[target]}' if text.strip()
                     else f"    {ids[source]} {arrow} {ids[target]}")  # fmt: skip
    nodes = [DiagramNode(ids[key], labels[key] or key, links[key]) for key in keys]
    arrows = [
        DiagramArrow(
            labels[relation.source_id] or relation.source_id, str(relation.kind),
            labels[relation.target_id] or relation.target_id, str(relation.attributes.get("evidence", "explicit")),
            _source_link(str(relation.attributes.get("source", ""))),
            tuple(str(name) for name in relation.attributes.get("names") or ()),
        )
        for relation in relations
    ]  # fmt: skip
    return Diagram("\n".join(lines) + "\n", nodes, rolled_up, arrows)


def _within(folder: str, scope: str) -> bool:
    return folder == scope or folder.startswith(scope + "/")


def _part_folder(entity: Entity) -> str:
    return str(entity.attributes.get("folder") or "")


def _source_link(cited: str) -> str | None:
    path, _, line = cited.partition("#L")
    if not path or not LISTABLE.fullmatch(path):
        return None
    return f"/source/{path}#L{line}" if line.isdigit() else f"/source/{path}"


MAX_AVAILABLE = 15  # of each kind, so the list stays a short part of a question's prompt
# A folder or project id listed for an answer: plain path characters only, so no repository name can carry
# whitespace, braces or instructions into the prompt (a name with spaces couldn't be drawn anyway).
LISTABLE = re.compile(r"[A-Za-z0-9._@+/:-]+")


def available_diagrams(store: FactStore) -> list[str]:
    """The diagram placeholders that would draw something from these facts, for an answer to use (design 7.3).

    The system first, when there are parts; imports for every top-level folder, project folder and folder of
    projects that holds at least two modules or Swift targets; dependencies for every project that has any;
    resources for every top-level Terraform folder.
    """
    located = [_location(entity.id) for kind in (EntityKind.MODULE, EntityKind.SWIFT_TARGET)
               for entity in store.entities(kind)]  # fmt: skip
    projects = [entity.id for entity in store.entities(EntityKind.PROJECT)]
    folders = {path.split("/", 1)[0] for path in located if "/" in path}
    for project in projects:
        folder = _location(project)
        if folder not in ("", "."):
            folders |= {folder, str(PurePosixPath(folder).parent)}
    scopes = sorted(
        folder
        for folder in folders
        if folder not in ("", ".") and sum(p.startswith(folder + "/") for p in located) >= 2
    )
    depending = {relation.source_id for relation in store.relations(RelationKind.DEPENDS_ON)}
    terraform = sorted(
        {_location(entity.id).split("/", 1)[0] for entity in store.entities(EntityKind.TERRAFORM_MODULE)}
    )
    scopes = [scope for scope in scopes if LISTABLE.fullmatch(scope)]
    depending = {project for project in depending if LISTABLE.fullmatch(project)}
    terraform = [scope for scope in terraform if LISTABLE.fullmatch(scope)]
    system = ["{{diagram system}}"] if store.entities(EntityKind.PART) else []
    return (
        system
        + [f"{{{{diagram imports scope={scope}}}}}" for scope in scopes[:MAX_AVAILABLE]]
        + [f"{{{{diagram dependencies project={project}}}}}" for project in sorted(depending & set(projects))][
            :MAX_AVAILABLE
        ]
        + [f"{{{{diagram resources scope={scope}}}}}" for scope in terraform[:MAX_AVAILABLE]]
    )


def _package_name(package_id: str) -> str:
    """package:<ecosystem>/<name> -> <name>, for every ecosystem (pypi, swift, ...)."""
    return package_id.split(":", 1)[-1].split("/", 1)[-1]


def _location(fact_id: str) -> str:
    """A path-based fact id's path: module:<path>, swift_target:<folder>/<name>, terraform_module:<folder>..."""
    return fact_id.split(":", 1)[1]


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
    lines += [_node(ids[key], labels[key], key) for key in keys]
    for (source, target), count in sorted(edges.items()):
        lines.append(_edge(ids[source], ids[target], str(count) if counted else ""))
    nodes = [DiagramNode(ids[key], labels[key], links[key]) for key in keys]
    return Diagram("\n".join(lines) + "\n", nodes, rolled_up)
