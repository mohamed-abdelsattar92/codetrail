"""The swift extractor: Swift packages, their targets, dependencies, and the imports of each target (design 5.2).

`Package.swift` is read for the package's name, its targets (with their source folder and dependencies) and the
packages it fetches by URL (without any credentials in it). The manifest is untrusted, so reading it stays linear:
brackets are paired in one pass, a target call inside another is skipped, and every pattern's tail is bounded.

Each `.swift` file belongs to the target whose folder holds it; its `import` lines, read with tree-sitter, become
edges from that target to another target or to an external package's product. Apple's frameworks (Foundation,
SwiftUI...) resolve to nothing and are counted once each.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath

import tree_sitter_swift
from tree_sitter import Language, Parser

from codetrail.extract import FileFacts, Reference, Resolution, without_credentials
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

SWIFT = Language(tree_sitter_swift.language())
TARGET_CALL = re.compile(r"\.(target|testTarget|executableTarget)\s*\(")
NAME = re.compile(r'\bname\s*:\s*"([^"]+)"')
PATH = re.compile(r'\bpath\s*:\s*"([^"]+)"')
PRODUCT = re.compile(r'\.product\s*\(\s*name\s*:\s*"([^"]+)"\s*,\s*package\s*:\s*"([^"]+)"[^)]{0,2000}\)')
NAMED_TARGET = re.compile(r'\.(?:target|byName)\s*\(\s*name\s*:\s*"([^"]+)"[^)]{0,2000}\)')
QUOTED = re.compile(r'"([^"]+)"')
REMOTE_PACKAGE = re.compile(r'\.package\s*\(\s*url\s*:\s*"([^"]+)"([^)]{0,2000})\)')
VERSION = re.compile(r'"([0-9][^"]*)"')
IMPORT_KINDS = {"typealias", "struct", "class", "enum", "protocol", "let", "var", "func"}


def package_id(name: str) -> str:
    return f"package:swift/{name.removesuffix('.git').lower()}"


def _balanced(text: str, start: int, opening: str, closing: str) -> str | None:
    """The text between the bracket at `start` and its match, or None when it never closes."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index]
    return None


def _closing_brackets(text: str) -> dict[int, int]:
    """Each "(" position paired with its ")", found in one pass; a bracket that never closes has no entry."""
    pairs, open_at = {}, []
    for index, character in enumerate(text):
        if character == "(":
            open_at.append(index)
        elif character == ")" and open_at:
            pairs[open_at.pop()] = index
    return pairs


def _calls(text: str) -> Iterator[tuple[str, str]]:
    """Each target call and its arguments; one inside another call's arguments is skipped, so no text is read twice."""
    closing = _closing_brackets(text)
    read_to = -1
    for match in TARGET_CALL.finditer(text):
        start = match.end() - 1
        if start > read_to and start in closing:
            read_to = closing[start]
            yield match.group(1), text[start + 1 : read_to]


class SwiftExtractor:
    name = "swift"
    version = 1

    def __init__(self) -> None:
        self._parser = Parser(SWIFT)

    def handles(self, path: str) -> bool:
        return path.endswith(".swift")

    def prepare(self, paths: Sequence[str]) -> None:
        return None

    def extract(self, path: str, content: bytes) -> FileFacts:
        if PurePosixPath(path).name == "Package.swift":
            return self._manifest(path, content.decode("utf-8", "replace"))
        imports = []
        for node in self._parser.parse(content).root_node.children:
            if node.type == "import_declaration":
                words = [word for word in (node.text or b"").decode().split() if not word.startswith("@")]
                words = [word for word in words[1:] if word not in IMPORT_KINDS]  # drop "import" and a kind
                if words:
                    line = node.start_point[0] + 1
                    imports.append(Reference(f"file:{path}", RelationKind.IMPORTS, words[0].split(".")[0],
                                             (Source(path, line, line),)))  # fmt: skip
        return FileFacts(path, (), tuple(imports))

    def _manifest(self, path: str, text: str) -> FileFacts:
        folder = str(PurePosixPath(path).parent)
        project = f"project:{folder}"
        package_call = text.find("Package(")
        named = NAME.search(text, package_call if package_call != -1 else 0)
        package_name = named.group(1) if named else PurePosixPath(folder).name
        source = (Source(path),)
        entities = [Entity(project, EntityKind.PROJECT, {"name": package_name, "language": "swift"}, source)]
        references: list[Reference] = []
        for url, rest in REMOTE_PACKAGE.findall(text):
            url = without_credentials(url)
            external = package_id(url.rstrip("/").rsplit("/", 1)[-1])
            entities.append(Entity(external, EntityKind.PACKAGE, {"url": url}, source))
            version = VERSION.search(rest)
            references.append(Reference(project, RelationKind.DEPENDS_ON, external, source,
                                        {"version": version.group(1) if version else ""}))  # fmt: skip
        for kind, body in _calls(text):
            name = NAME.search(body)
            if name is None:
                continue
            target_name = name.group(1)
            explicit = PATH.search(body)
            default = ("Tests" if kind == "testTarget" else "Sources") + f"/{target_name}"
            target_path = f"{folder}/{explicit.group(1) if explicit else default}".removeprefix("./")
            target = f"swift_target:{folder}/{target_name}"
            entities.append(Entity(target, EntityKind.SWIFT_TARGET,
                                   {"name": target_name, "kind": "test" if kind == "testTarget" else "target",
                                    "package": package_name, "path": target_path}, source))  # fmt: skip
            references.append(Reference(project, RelationKind.CONTAINS, target, source))
            start = body.find("dependencies:")
            bracket = body.find("[", start) if start != -1 else -1
            dependencies = _balanced(body, bracket, "[", "]") if bracket != -1 else None
            if dependencies is None:
                continue
            for product, package in PRODUCT.findall(dependencies):
                references.append(Reference(target, RelationKind.DEPENDS_ON, package_id(package), source,
                                            {"product": product}))  # fmt: skip
            remaining = PRODUCT.sub("", dependencies)
            names = NAMED_TARGET.findall(remaining) + QUOTED.findall(NAMED_TARGET.sub("", remaining))
            for dependency in names:
                references.append(Reference(target, RelationKind.DEPENDS_ON, f"target-name:{dependency}", source))
        return FileFacts(path, tuple(entities), tuple(references))

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        targets = [entity for entity in known.values() if entity.kind is EntityKind.SWIFT_TARGET]
        products = {
            str(reference.attributes["product"]): reference.target
            for file in files
            for reference in file.references
            if "product" in reference.attributes
        }

        def by_name(name: str, near: str) -> str | None:
            matches = [target for target in targets if target.attributes.get("name") == name]
            same = [target for target in matches if target.id.rsplit("/", 1)[0] == near.rsplit("/", 1)[0]]
            chosen = same or matches
            return chosen[0].id if chosen else None

        def owner(path: str) -> str | None:
            """The target whose folder holds the file; the deepest one when folders nest."""
            holders = [target for target in targets if path.startswith(str(target.attributes.get("path", "")) + "/")]
            holders.sort(key=lambda target: len(str(target.attributes.get("path", ""))))
            return holders[-1].id if holders else None

        relations: dict[tuple[str, str, str], Relation] = {}
        unresolved_names: set[str] = set()
        for file in files:
            for reference in file.references:
                source_id = reference.source_id
                if source_id.startswith("file:"):
                    holder = owner(source_id.removeprefix("file:"))
                    if holder is None:
                        continue
                    source_id = holder
                target = reference.target
                if target.startswith("target-name:") or reference.kind is RelationKind.IMPORTS:
                    name = target.removeprefix("target-name:")
                    target = by_name(name, source_id) or products.get(name) or ""
                    if not target:
                        unresolved_names.add(name)
                        continue
                if target in known and source_id in known and target != source_id:
                    relation = Relation(source_id, reference.kind, target, reference.attributes, reference.sources)
                    relations.setdefault(relation.key, relation)
        return Resolution(list(relations.values()), len(unresolved_names))
