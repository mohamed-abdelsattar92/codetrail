"""The openapi extractor: an OpenAPI document's routes, its schemas, and what uses what (design section 5.2).

Only local references (`#/components/schemas/<name>`) become relations; a remote or file `$ref` is never followed.
Documents are walked iteratively, to a fixed depth, so no nesting can exhaust the stack. YAML aliases are refused:
they let a few hundred bytes stand for an unbounded tree (or a loop), and OpenAPI documents don't need them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from pathspec import GitIgnoreSpec

from codetrail.extract import FileFacts, Reference, Resolution, load_yaml_without_aliases
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source

METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
SCHEMA_REF = "#/components/schemas/"
MAX_DEPTH = 64


def local_refs(value: Any) -> Iterator[str]:
    """The schema names of every local $ref inside `value`, walked iteratively to MAX_DEPTH."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        if isinstance(node, Mapping):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith(SCHEMA_REF):
                yield ref.removeprefix(SCHEMA_REF)
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class OpenApiExtractor:
    name = "openapi"
    version = 1

    def __init__(self, globs: Sequence[str]) -> None:
        self._spec = GitIgnoreSpec.from_lines(globs)

    def handles(self, path: str) -> bool:
        return PurePosixPath(path).suffix in (".json", ".yaml", ".yml") and self._spec.match_file(path)

    def prepare(self, paths: Sequence[str]) -> None:
        return None

    def extract(self, path: str, content: bytes) -> FileFacts:
        text = content.decode("utf-8")
        document = json.loads(text) if path.endswith(".json") else load_yaml_without_aliases(text)[1]
        if not isinstance(document, Mapping) or not ("openapi" in document or "swagger" in document):
            return FileFacts(path)
        entities: list[Entity] = []
        references: list[Reference] = []
        source = (Source(path),)
        for route_path, operations in (document.get("paths") or {}).items():
            if not isinstance(operations, Mapping):
                continue
            for method, operation in operations.items():
                if method not in METHODS or not isinstance(operation, Mapping):
                    continue
                route_id = f"route:{method.upper()} {route_path}"
                attributes = {
                    "method": method.upper(), "path": str(route_path),
                    "operation_id": _text(operation.get("operationId")), "summary": _text(operation.get("summary")),
                    "tags": [str(tag) for tag in operation.get("tags", []) if isinstance(tag, str)], "document": path,
                }  # fmt: skip
                entities.append(Entity(route_id, EntityKind.ROUTE, attributes, source))
                for name in sorted(set(local_refs(operation))):
                    references.append(Reference(route_id, RelationKind.USES_SCHEMA, f"schema:{name}", source))
        schemas = ((document.get("components") or {}).get("schemas")) or {}
        for name, schema in schemas.items():
            schema_id = f"schema:{name}"
            properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
            attributes = {"document": path, "properties": sorted(str(key) for key in properties)[:50]}
            entities.append(Entity(schema_id, EntityKind.SCHEMA, attributes, source))
            for referenced in sorted(set(local_refs(schema)) - {str(name)}):
                references.append(Reference(schema_id, RelationKind.REFERENCES, f"schema:{referenced}", source))
        return FileFacts(path, tuple(entities), tuple(references))

    def resolve(self, files: Sequence[FileFacts], known: Mapping[str, Entity]) -> Resolution:
        relations, unresolved = [], 0
        for file in files:
            for reference in file.references:
                if reference.target in known:
                    relations.append(
                        Relation(reference.source_id, reference.kind, reference.target, {}, reference.sources)
                    )
                else:
                    unresolved += 1
        return Resolution(relations, unresolved)
