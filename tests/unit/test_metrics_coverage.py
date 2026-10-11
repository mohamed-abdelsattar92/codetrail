"""Facts a document mentions, and facts the guide explains (design section 18.1)."""

from codetrail.facts import Entity, EntityKind, Source
from codetrail.guide import Page
from codetrail.metrics.coverage import measure_explained, measure_mentions, words_of


def entity(entity_id: str, kind: EntityKind, path: str = "x") -> Entity:
    return Entity(entity_id, kind, {}, (Source(path),))


def mentioned(entities: list[Entity], documents: dict[str, str]) -> list[str]:
    return [found.id for found in measure_mentions(entities, documents).mentioned]


def test_a_package_is_mentioned_by_its_name_as_a_whole_word() -> None:
    fastapi = [entity("package:pypi/fastapi", EntityKind.PACKAGE)]
    assert mentioned(fastapi, {"README.md": "We use FastAPI."})
    assert mentioned(fastapi, {"README.md": "Built on fastapi/starlette."})
    assert not mentioned(fastapi, {"README.md": "fastapix and fastapi-users"})
    assert mentioned([entity("package:pypi/pyyaml", EntityKind.PACKAGE)], {"a.md": "Read with PyYAML."})
    assert mentioned([entity("package:npm/@scope/ui-kit", EntityKind.PACKAGE)], {"a.md": "From `@scope/ui-kit`."})
    assert mentioned([entity("package:pypi/tree-sitter", EntityKind.PACKAGE)], {"a.md": "tree_sitter parses."})


def test_a_resource_is_mentioned_by_its_address() -> None:
    bucket = [entity("resource:infra/aws_s3_bucket.assets", EntityKind.RESOURCE)]
    assert mentioned(bucket, {"docs/infra.md": "The `aws_s3_bucket.assets` bucket holds images."})
    assert not mentioned(bucket, {"docs/infra.md": "The assets bucket holds images."})


def test_a_project_is_mentioned_by_a_readme_in_its_folder_or_by_its_folder() -> None:
    api = [entity("project:services/api", EntityKind.PROJECT)]
    assert mentioned(api, {"services/api/README.md": "# Orders"})
    assert mentioned(api, {"docs/map.md": "See services/api/ for the orders."})
    assert not mentioned(api, {"services/api/notes/README.md": "# Notes", "docs/map.md": "The api."})
    root = [entity("project:.", EntityKind.PROJECT)]
    assert mentioned(root, {"readme.rst": "Shop"})
    assert not mentioned(root, {"docs/x.md": "The . project"})


def test_only_packages_projects_and_resources_are_counted() -> None:
    metric = measure_mentions(
        [entity("module:app/db.py", EntityKind.MODULE), entity("package:pypi/httpx", EntityKind.PACKAGE)], {}
    )
    assert ([found.id for found in metric.unmentioned], metric.mentioned) == (["package:pypi/httpx"], [])


def test_replacement_characters_from_a_binary_document_are_just_text() -> None:
    assert "png" in words_of("��PNG\x00\x1a")


FACTS = [
    entity("module:app/db.py", EntityKind.MODULE, "app/db.py"),
    entity("decision:ADR-0001", EntityKind.DECISION, "docs/adr/0001-x.md"),
    entity("module:lib/x.py", EntityKind.MODULE, "lib/x.py"),
    entity("package:pypi/httpx", EntityKind.PACKAGE, "app/pyproject.toml"),
]


def test_facts_in_a_written_pages_scope_are_explained() -> None:
    pages = [
        Page("areas/app", {"kind": "area", "title": "App", "scope": {"paths": ["app"], "kinds": ["module"]}}),
        Page("concepts/x", {"kind": "concept", "title": "X", "facts": [{"id": "decision:ADR-0001", "hash": "h"}]}),
        Page("digests/d", {"kind": "digest", "title": "D", "scope": {"paths": [""]}}),
    ]
    metric = measure_explained(FACTS, pages)
    assert (metric.explained, metric.total) == (2, 4)
    assert [found.id for found in metric.unexplained] == ["module:lib/x.py", "package:pypi/httpx"]


def test_a_page_with_unusable_scope_explains_nothing() -> None:
    pages = [
        Page("areas/a", {"kind": "area", "title": "A", "scope": "app"}),
        Page("areas/b", {"kind": "area", "title": "B", "scope": {"paths": "app"}, "facts": [3, {"no": "id"}]}),
    ]
    assert measure_explained(FACTS, pages).explained == 0
