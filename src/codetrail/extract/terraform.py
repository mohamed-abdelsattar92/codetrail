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


PATH_ATTRIBUTE = re.compile(r'\b(?:source_dir|source|context|dockerfile|path|working_dir)\s*=\s*"([^"\n]*)"')
MAX_PATHS = 20


class TerraformExtractor:
    name = "terraform"
    version = 2  # resources record their `paths`

    def __init__(self) -> None:
        self._parser = Parser(HCL)

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
                found = _paths(folder, (block.text or b"").decode("utf-8", "replace"))
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


def _paths(folder: str, text: str) -> list[str]:
    """Path-like attribute values in the repository, resolved lexically against the module's folder.

    Values containing `$` (interpolation), absolute paths and paths climbing above the repository are dropped.
    """
    found: set[str] = set()
    for value in PATH_ATTRIBUTE.findall(text):
        if not value or "$" in value or value.startswith("/") or "://" in value:
            continue
        joined = posixpath.normpath(posixpath.join(folder, value))
        if joined == ".." or joined.startswith("../"):
            continue
        found.add("" if joined == "." else joined)
    return sorted(found)[:MAX_PATHS]


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
