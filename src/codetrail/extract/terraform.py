"""The terraform extractor: modules, resources, module calls and references (design section 5.2).

Every folder holding `.tf` files is a Terraform module. `resource` blocks are resources the module contains; a
reference such as `google_storage_bucket.audio.name` inside a resource is an edge to that resource in the same module;
a `module` block whose `source` is a relative path is an edge to the module folder it names, when that folder is in
the repository. Registry and remote sources are counted as unresolved, never fetched.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

import tree_sitter_hcl
from tree_sitter import Language, Node, Parser

from codetrail.extract import FileFacts, Reference, Resolution
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

HCL = Language(tree_sitter_hcl.language())
REFERENCE = re.compile(r"\b([a-z][a-z0-9]*_[a-z0-9_]+)\.([A-Za-z_][A-Za-z0-9_-]*)\b")


def module_id(folder: str) -> str:
    return f"terraform_module:{folder}"


PATH_NAMES = {b"source_dir", b"source", b"context", b"dockerfile", b"path", b"working_dir"}
PATH_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


class TerraformExtractor:
    name = "terraform"
    version = 2  # resources record their `paths`

    def __init__(self, max_paths: int = 20) -> None:
        self._parser = Parser(HCL)
        self._max_paths = max_paths

    def handles(self, path: str) -> bool:
        return path.endswith(".tf")

    def prepare(self, paths: Sequence[str]) -> None:
        return None

    def extract(self, path: str, content: bytes) -> FileFacts:
        folder = str(PurePosixPath(path).parent)
        module = module_id(folder)
        entities = [Entity(module, EntityKind.TERRAFORM_MODULE, {"name": PurePosixPath(folder).name or folder})]
        references: list[Reference] = []
        root = self._parser.parse(content).root_node
        body = next((child for child in root.children if child.type == "body"), None)
        for block in body.children if body is not None else []:
            if block.type != "block":
                continue
            kind, labels = _block_head(block)
            line = block.start_point[0] + 1
            source = (Source(path, line, block.end_point[0] + 1),)
            if kind == "resource" and len(labels) == 2:
                resource = f"resource:{folder}/{labels[0]}.{labels[1]}"
                attributes: dict[str, object] = {"type": labels[0], "name": labels[1], "module": folder}
                found = _paths(folder, block, self._max_paths)
                if found:
                    attributes["paths"] = found
                entities.append(Entity(resource, EntityKind.RESOURCE, attributes, source))
                references.append(Reference(module, RelationKind.CONTAINS, resource, source))
                text = (block.text or b"").decode("utf-8", "replace")
                for match in sorted(set(REFERENCE.findall(text))):
                    if match != (labels[0], labels[1]):
                        target = f"resource:{folder}/{match[0]}.{match[1]}"
                        references.append(Reference(resource, RelationKind.REFERENCES, target, source))
            elif kind == "module" and labels:
                called = _module_source(block)
                if called is not None and called.startswith("."):
                    target_folder = posixpath.normpath(posixpath.join(folder, called))
                    references.append(Reference(module, RelationKind.REFERENCES, module_id(target_folder), source,
                                                {"call": labels[0]}))  # fmt: skip
                else:
                    references.append(Reference(module, RelationKind.REFERENCES, "", source, {"call": labels[0]}))
        return FileFacts(path, tuple(entities), tuple(references))

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        relations: list[Relation] = []
        unresolved = 0
        for file in files:
            for reference in file.references:
                if reference.target in known:
                    relations.append(Relation(reference.source_id, reference.kind, reference.target,
                                              reference.attributes, reference.sources))  # fmt: skip
                elif reference.kind is RelationKind.REFERENCES and reference.source_id.startswith("terraform_module:"):
                    unresolved += 1  # a registry, remote or missing module
                # a resource name that matches no resource (a data source, a provider attribute) is not a reference
        return Resolution(relations, unresolved)


def _paths(folder: str, block: Node, limit: int) -> list[str]:
    """Plain-string path attributes of the block (nested blocks too), resolved lexically against the module's folder.

    Only an attribute whose value is one plain string counts: no interpolation, heredoc or comment, every part a plain
    path segment (so nothing credential-shaped), and nothing absolute or climbing above the repository.
    """
    found: set[str] = set()
    stack = [block]
    while stack:
        node = stack.pop()
        for child in node.children:
            if child.type in ("body", "block"):
                stack.append(child)
            elif child.type == "attribute":
                name = next((part for part in child.children if part.type == "identifier"), None)
                value = _plain_string(child) if name is not None and name.text in PATH_NAMES else None
                if value is None or value.startswith("/"):
                    continue
                parts = [part for part in value.split("/") if part not in ("", ".")]
                if not parts or any(part != ".." and not PATH_SEGMENT.fullmatch(part) for part in parts):
                    continue
                joined = posixpath.normpath(posixpath.join(folder, value))
                if joined != ".." and not joined.startswith("../"):
                    found.add("" if joined == "." else joined)
    return sorted(found)[:limit]


def _plain_string(attribute: Node) -> str | None:
    """The attribute's value when it is exactly one quoted string with no interpolation."""
    expression = next((part for part in attribute.children if part.type == "expression"), None)
    literal = expression.children[0] if expression is not None and expression.child_count == 1 else None
    string = (
        literal.children[0]
        if literal is not None and literal.type == "literal_value" and literal.child_count == 1
        else None
    )
    if string is None or string.type != "string_lit":
        return None
    texts = [part for part in string.children if part.type == "template_literal"]
    others = [part for part in string.children if part.type not in ("template_literal", "quoted_template_start",
                                                                  "quoted_template_end")]  # fmt: skip
    return (texts[0].text or b"").decode("utf-8", "replace") if len(texts) == 1 and not others else None


def _block_head(block: Node) -> tuple[str, list[str]]:
    kind = ""
    labels = []
    for child in block.children:
        if child.type == "identifier" and not kind:
            kind = (child.text or b"").decode()
        elif child.type == "string_lit":
            labels.append(
                "".join((part.text or b"").decode() for part in child.children if part.type == "template_literal")
            )
        elif child.type == "block_start":
            break
    return kind, labels


def _module_source(block: Node) -> str | None:
    body = next((child for child in block.children if child.type == "body"), None)
    for attribute in body.children if body is not None else []:
        if attribute.type != "attribute":
            continue
        name = next((child for child in attribute.children if child.type == "identifier"), None)
        if name is not None and name.text == b"source":
            literal = re.search(rb'"([^"]*)"', attribute.text or b"")
            return literal.group(1).decode() if literal else None
    return None
