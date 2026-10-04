"""The python extractor: projects, modules, packages and imports (design section 5.2).

Every `pyproject.toml` marks a project root. A `.py` file belongs to the nearest root above it, and its dotted name is
its path from that root, with a leading `src/` dropped and `__init__` naming its package. Imports are read with
tree-sitter, which tolerates syntax errors, and resolve to the project's own modules first, then to a declared
package by top-level name. Standard-library imports are dropped; anything else is counted as unresolved.
"""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from codetrail.extract import FileFacts, Reference, Resolution
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

PYTHON = Language(tree_sitter_python.language())
REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([^;]*)")
# Import names that differ from the distribution that provides them.
IMPORT_TO_DISTRIBUTION = {
    "yaml": "pyyaml",
    "jwt": "pyjwt",
    "dotenv": "python-dotenv",
    "PIL": "pillow",
    "dateutil": "python-dateutil",
    "multipart": "python-multipart",
    "magic": "python-magic",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
}


def normalise_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def project_id(root: str) -> str:
    return f"project:{root or '.'}"


class PythonExtractor:
    name = "python"
    version = 1

    def __init__(self) -> None:
        self._roots: list[str] = []
        self._parser = Parser(PYTHON)

    def handles(self, path: str) -> bool:
        return path.endswith(".py") or PurePosixPath(path).name == "pyproject.toml"

    def prepare(self, paths: Sequence[str]) -> None:
        roots = [str(PurePosixPath(path).parent) for path in paths if PurePosixPath(path).name == "pyproject.toml"]
        # Deepest first, so a nested project wins over the one around it.
        self._roots = sorted((("" if root == "." else root) for root in roots), key=len, reverse=True)

    def extract(self, path: str, content: bytes) -> FileFacts:
        if PurePosixPath(path).name == "pyproject.toml":
            return self._extract_project(path, content)
        return self._extract_module(path, content)

    def _extract_project(self, path: str, content: bytes) -> FileFacts:
        data = tomllib.loads(content.decode("utf-8"))
        root = str(PurePosixPath(path).parent)
        project = project_id("" if root == "." else root)
        table = data.get("project", {})
        attributes = {
            key: value
            for key, value in (("name", table.get("name")), ("requires_python", table.get("requires-python")))
            if isinstance(value, str) and value
        }
        entities = [Entity(project, EntityKind.PROJECT, attributes, (Source(path),))]
        references = []
        groups: dict[str, list[Any]] = {"main": table.get("dependencies", [])}
        groups.update(data.get("dependency-groups", {}))
        for group, requirements in groups.items():
            for requirement in requirements:
                match = REQUIREMENT.match(requirement) if isinstance(requirement, str) else None
                if not match:
                    continue
                package = f"package:pypi/{normalise_distribution(match.group(1))}"
                entities.append(Entity(package, EntityKind.PACKAGE, {}, (Source(path),)))
                references.append(
                    Reference(
                        project,
                        RelationKind.DEPENDS_ON,
                        package,
                        (Source(path),),
                        {"specifier": match.group(3).strip(), "group": group},
                    )
                )
        return FileFacts(path, tuple(entities), tuple(references))  # fmt: skip

    def _extract_module(self, path: str, content: bytes) -> FileFacts:
        root = self._root_of(path)
        if root is None:
            return FileFacts(path)
        name = self._module_name(path, root)
        module_id = f"module:{path}"
        module = Entity(module_id, EntityKind.MODULE, {"name": name, "project": project_id(root)}, (Source(path),))
        package = name.split(".") if path.endswith("__init__.py") else name.split(".")[:-1]
        references = [Reference(project_id(root), RelationKind.CONTAINS, module_id, (Source(path),))]
        for target, names, line in self._imports(content, package):
            references.append(
                Reference(module_id, RelationKind.IMPORTS, target, (Source(path, line, line),), {"names": names})
            )
        return FileFacts(path, (module,), tuple(references))

    def _root_of(self, path: str) -> str | None:
        for root in self._roots:
            if not root or path.startswith(root + "/"):
                return root
        return None

    @staticmethod
    def _module_name(path: str, root: str) -> str:
        relative = PurePosixPath(path[len(root) + 1 :] if root else path)
        parts = list(relative.with_suffix("").parts)
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _imports(self, content: bytes, package: list[str]) -> Iterator[tuple[str, list[str], int]]:
        """(module, imported names, line) for every import statement, relative ones made absolute."""
        for node in _walk(self._parser.parse(content).root_node):
            line = node.start_point[0] + 1
            if node.type == "import_statement":
                for child in node.children_by_field_name("name"):
                    yield _dotted(child), [], line
            elif node.type == "import_from_statement":
                module_node = node.child_by_field_name("module_name")
                if module_node is None:
                    continue
                names = [_dotted(child) for child in node.children_by_field_name("name")]
                if module_node.type == "relative_import":
                    level = sum(
                        len(child.text or b"") for child in module_node.children if child.type == "import_prefix"
                    )
                    rest = [child for child in module_node.children if child.type == "dotted_name"]
                    base = package[: len(package) - (level - 1)] if level - 1 <= len(package) else []
                    target = ".".join([*base, *([_dotted(rest[0])] if rest else [])])
                else:
                    target = _dotted(module_node)
                if target:
                    yield target, names, line

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        modules: dict[str, dict[str, str]] = {}  # project -> dotted name -> module id
        for entity in known.values():
            if entity.kind is EntityKind.MODULE:
                modules.setdefault(str(entity.attributes["project"]), {})[str(entity.attributes["name"])] = entity.id
        relations: list[Relation] = []
        unresolved = 0
        for file in files:
            for reference in file.references:
                if reference.kind is not RelationKind.IMPORTS:
                    relations.append(Relation(reference.source_id, reference.kind, reference.target,
                                              reference.attributes, reference.sources))  # fmt: skip
                    continue
                source = known.get(reference.source_id)
                project = str(source.attributes["project"]) if source else ""
                names = list(reference.attributes.get("names", []))
                targets: list[str] | None = _local_targets(reference.target, names, modules.get(project, {}))
                if not targets:
                    targets = _package_target(reference.target, known)
                if targets is None:
                    unresolved += 1
                    continue
                relations.extend(
                    Relation(reference.source_id, RelationKind.IMPORTS, target, {}, reference.sources)
                    for target in targets
                    if target != reference.source_id
                )
        return Resolution(relations, unresolved)


def _local_targets(target: str, names: list[str], modules: Mapping[str, str]) -> list[str]:
    """The project's modules an import reaches: submodules named in a from-import, else the longest module prefix."""
    found = [modules[f"{target}.{name}"] for name in names if f"{target}.{name}" in modules]
    if found and len(found) == len(names):
        return found
    parts = target.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in modules:
            return [*found, modules[candidate]]
    return found


def _package_target(target: str, known: Mapping[str, Entity]) -> list[str] | None:
    """A declared package for a third-party import, [] for the standard library, None when unknown."""
    top = target.split(".")[0]
    if top in sys.stdlib_module_names or top == "__future__":
        return []
    package = f"package:pypi/{normalise_distribution(IMPORT_TO_DISTRIBUTION.get(top, top))}"
    return [package] if package in known else None


def _dotted(node: Node) -> str:
    if node.type == "aliased_import":
        name = node.child_by_field_name("name")
        node = name if name is not None else node
    return (node.text or b"").decode("utf-8", "replace")


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))
