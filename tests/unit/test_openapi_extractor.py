"""The openapi extractor: routes, schemas and what uses what (design section 5.2)."""

import json
from pathlib import Path

from codetrail.extract import run_extractors
from codetrail.extract.openapi import OpenApiExtractor
from codetrail.facts import EntityKind, RelationKind

DOCUMENT = {
    "openapi": "3.1.0",
    "paths": {
        "/notes": {
            "post": {
                "operationId": "create_note",
                "summary": "Create a note",
                "tags": ["notes"],
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/NoteIn"}}}},
                "responses": {
                    "201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Note"}}}}
                },
            },
            "get": {"operationId": "list_notes", "responses": {"200": {"description": "ok"}}},
            "parameters": [],
        }
    },
    "components": {
        "schemas": {
            "Note": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}, "owner": {"$ref": "#/components/schemas/User"}},
            },
            "NoteIn": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "remote": {"$ref": "https://evil.example/x.json"}},
            },
            "User": {"type": "object", "properties": {"id": {"type": "string"}}},
        }
    },
}


def run(root: Path, files: dict[str, str]):  # type: ignore[no-untyped-def]
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return run_extractors(root, sorted(files), [OpenApiExtractor(["**/openapi.json", "**/openapi.yaml"])])


def test_routes_schemas_and_uses(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"packages/contracts/openapi.json": json.dumps(DOCUMENT)})
    entities = {entity.id: entity for entity in extraction.entities}
    assert entities["route:POST /notes"].attributes == {
        "method": "POST", "path": "/notes", "operation_id": "create_note", "summary": "Create a note",
        "tags": ["notes"], "document": "packages/contracts/openapi.json",
    }  # fmt: skip
    assert "route:GET /notes" in entities
    assert entities["schema:Note"].kind is EntityKind.SCHEMA
    edges = {(r.source_id, r.kind, r.target_id) for r in extraction.relations}
    assert ("route:POST /notes", RelationKind.USES_SCHEMA, "schema:NoteIn") in edges
    assert ("route:POST /notes", RelationKind.USES_SCHEMA, "schema:Note") in edges
    assert ("schema:Note", RelationKind.REFERENCES, "schema:User") in edges
    assert not any("evil" in target for _, _, target in edges)


def test_yaml_documents(tmp_path: Path) -> None:
    extraction = run(
        tmp_path, {"api/openapi.yaml": "openapi: 3.0.0\npaths:\n  /x:\n    get:\n      operationId: get_x\n"}
    )
    assert [entity.id for entity in extraction.entities] == ["route:GET /x"]


def test_a_broken_document_is_a_warning(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"openapi.json": "{ not json"})
    assert extraction.warnings and extraction.entities == []


def test_deep_nesting_stays_bounded(tmp_path: Path) -> None:
    deep: dict[str, object] = {"type": "object"}
    for _ in range(500):
        deep = {"type": "object", "properties": {"x": deep}}
    document = {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {"Deep": deep}}}
    extraction = run(tmp_path, {"openapi.json": json.dumps(document)})
    assert [entity.id for entity in extraction.entities] == ["schema:Deep"]
