"""The system pass: parts, connections and their evidence, from facts only (design section 17.3)."""

from collections.abc import Mapping

import pytest

from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.system import SystemFacts, derive


def entity(fact_id: str, entity_kind: EntityKind, path: str, **attributes: object) -> Entity:
    return Entity(fact_id, entity_kind, attributes, (Source(path, 1, 1),))


ENTITIES = [
    entity("project:services/api", EntityKind.PROJECT, "services/api/pyproject.toml", name="api"),
    entity("package:pypi/fastapi", EntityKind.PACKAGE, "services/api/pyproject.toml"),
    entity("module:services/api/app/main.py", EntityKind.MODULE, "services/api/app/main.py", name="app.main"),
    entity("project:apps/site", EntityKind.PROJECT, "apps/site/package.json", name="@shop/site"),
    entity("package:npm/astro", EntityKind.PACKAGE, "apps/site/package.json"),
    entity("module:apps/site/src/index.ts", EntityKind.MODULE, "apps/site/src/index.ts", name="apps/site/src/index.ts"),
    entity("worker:apps/site", EntityKind.WORKER, "apps/site/wrangler.jsonc", name="shop-site"),
    entity("project:packages/ui", EntityKind.PROJECT, "packages/ui/package.json", name="@shop/ui"),
    entity("module:packages/ui/index.ts", EntityKind.MODULE, "packages/ui/index.ts", name="packages/ui/index.ts"),
    entity(
        "project:apps/ios/Packages/APIClient",
        EntityKind.PROJECT,
        "apps/ios/Packages/APIClient/Package.swift",
        name="APIClient",
    ),
    entity("route:GET /orders", EntityKind.ROUTE, "contracts/openapi.yaml"),
    entity("schema:Order", EntityKind.SCHEMA, "contracts/openapi.yaml"),
    entity("route:apps/site GET /", EntityKind.ROUTE, "apps/site/src/pages/index.astro", project="project:apps/site"),
    entity("terraform_module:infra/app", EntityKind.TERRAFORM_MODULE, "infra/app/main.tf"),
    entity(
        "resource:infra/app/google_cloud_run_v2_service.api",
        EntityKind.RESOURCE,
        "infra/app/run.tf",
        module="infra/app",
        name="api",
        type="google_cloud_run_v2_service",
    ),
    entity(
        "resource:infra/app/google_storage_bucket.media",
        EntityKind.RESOURCE,
        "infra/app/run.tf",
        module="infra/app",
        name="media",
        type="google_storage_bucket",
        paths=["services/api"],
    ),
    entity(
        "deployment:.github/workflows/deploy.yml#site/2",
        EntityKind.DEPLOYMENT,
        ".github/workflows/deploy.yml",
        kind="cloudflare",
        folder="apps/site",
    ),
    entity(
        "deployment:.github/workflows/deploy.yml#odd/0",
        EntityKind.DEPLOYMENT,
        ".github/workflows/deploy.yml",
        kind="fly",
    ),  # no folder: connects to nothing
]
RELATIONS = [
    Relation("project:services/api", RelationKind.DEPENDS_ON, "package:pypi/fastapi"),
    Relation("project:apps/site", RelationKind.DEPENDS_ON, "package:npm/astro"),
    Relation(
        "project:apps/site", RelationKind.DEPENDS_ON, "project:packages/ui", {}, (Source("apps/site/package.json"),)
    ),
    Relation(
        "module:apps/site/src/index.ts",
        RelationKind.IMPORTS,
        "module:packages/ui/index.ts",
        {},
        (Source("apps/site/src/index.ts", 3, 3),),
    ),
]
FILES = {
    "contracts/openapi.yaml": "blob-contract",
    "apps/ios/Packages/APIClient/Sources/APIClient/openapi.yaml": "blob-contract",  # the generator's copy
    "apps/site/package.json": "b1",
    "apps/site/wrangler.jsonc": "b2",
    "apps/ios/Shop.xcodeproj/project.pbxproj": "b3",
    "services/api/pyproject.toml": "b4",
    "services/api/Makefile": "b5",
}
CONTENT = {
    "apps/site/package.json": b'{"scripts": {"types": "openapi-typescript ../../contracts/openapi.yaml -o t.ts"}}',
    "apps/ios/Shop.xcodeproj/project.pbxproj": (
        b'\t\trelativePath = Packages/APIClient;\n\t\trelativePath = "../../../etc";\n'
    ),
    "services/api/Makefile": b"check:\n\tvalidate ../../contracts/openapi.yaml\n",
}


def reader(content: Mapping[str, bytes]):  # type: ignore[no-untyped-def]
    return lambda path: content.get(path) if path in FILES else None


@pytest.fixture
def system() -> SystemFacts:
    return derive(ENTITIES, RELATIONS, FILES, reader(CONTENT))


def parts(found: SystemFacts) -> dict[str, str]:
    return {entity.id: str(entity.attributes["kind"]) for entity in found.entities}


def arrows(found: SystemFacts) -> dict[tuple[str, str, str], dict[str, object]]:
    return {(r.source_id, str(r.kind), r.target_id): dict(r.attributes) for r in found.relations}


def test_parts_and_their_kinds(system: SystemFacts) -> None:
    assert parts(system) == {
        "part:services/api": "service",  # depends on FastAPI
        "part:apps/site": "app",  # depends on Astro
        "part:packages/ui": "library",
        "part:apps/ios/Packages/APIClient": "library",
        "part:apps/ios": "app",  # an Xcode project
        "part:contracts/openapi.yaml": "contract",
        "part:infra/app": "infrastructure",
        "part:platform:google_cloud": "platform",
        "part:platform:cloudflare": "platform",
    }


def test_dependencies_across_parts_are_explicit(system: SystemFacts) -> None:
    found = arrows(system)
    assert found[("part:apps/site", "depends_on", "part:packages/ui")]["evidence"] == "explicit"
    assert found[("part:apps/ios", "depends_on", "part:apps/ios/Packages/APIClient")]["source"] == (
        "apps/ios/Shop.xcodeproj/project.pbxproj#L1"
    )
    assert not any(target == "part:." for (_, _, target) in found)  # "../../../etc" leaves the repository


def test_contracts_are_implemented_or_called(system: SystemFacts) -> None:
    found = arrows(system)
    assert (
        found[("part:services/api", "implements", "part:contracts/openapi.yaml")]["source"]
        == "services/api/Makefile#L2"
    )
    assert ("part:apps/site", "calls_via", "part:contracts/openapi.yaml") in found  # named in a package.json script
    copy = found[("part:apps/ios/Packages/APIClient", "calls_via", "part:contracts/openapi.yaml")]
    assert copy["source"] == "apps/ios/Packages/APIClient/Sources/APIClient/openapi.yaml"  # the same blob


def test_deployments_explicit_and_matched(system: SystemFacts) -> None:
    found = arrows(system)
    assert found[("part:apps/site", "deployed_on", "part:platform:cloudflare")]["evidence"] == "explicit"
    assert found[("part:infra/app", "deployed_on", "part:platform:google_cloud")]["evidence"] == "explicit"
    assert found[("part:services/api", "deployed_on", "part:infra/app")]["evidence"] == "explicit"  # terraform paths
    matched = found[("part:services/api", "deployed_on", "part:platform:google_cloud")]
    assert matched["evidence"] == "matched" and matched["names"] == ["api", "services/api"]


def test_a_deployment_without_a_folder_connects_nothing(system: SystemFacts) -> None:
    assert "part:platform:fly" not in parts(system)


def test_a_repository_without_parts_gets_none() -> None:
    found = derive([], [], {}, lambda path: None)
    assert (found.entities, found.relations, found.warnings) == ([], [], [])


def test_the_reader_is_the_only_way_to_files() -> None:
    read_paths: list[str] = []

    def spy(path: str) -> bytes | None:
        read_paths.append(path)
        return CONTENT.get(path)

    derive(ENTITIES, RELATIONS, FILES, spy)
    assert set(read_paths) <= set(FILES)


def test_hostile_facts_never_raise() -> None:
    hostile = [
        Entity("project:a\nb {{x}}", EntityKind.PROJECT, {"name": "x\n%%{init}%%"}, ()),
        Entity("resource:infra/x", EntityKind.RESOURCE, {"type": 7, "paths": "not a list"}, (Source("x"),)),
        Entity("deployment:x", EntityKind.DEPLOYMENT, {"kind": "unknown", "folder": "a"}, (Source("x"),)),
    ]
    found = derive(hostile, [Relation("project:a\nb {{x}}", RelationKind.DEPENDS_ON, "nowhere")], {}, lambda p: None)
    assert isinstance(found, SystemFacts)


def test_hostile_lines_and_names_take_linear_time() -> None:
    import time

    line = b"relativePath = " * 20_000  # no `;`: a backtracking pattern would rescan the line from every match
    files = {**FILES, ("generator" * 2_000) + "x": "b9"}
    started = time.monotonic()
    derive(ENTITIES, RELATIONS, files, reader({**CONTENT, "apps/ios/Shop.xcodeproj/project.pbxproj": line}))
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Makefile", True),
        ("buf.gen.toml", True),
        ("openapi-generator-config.yaml", True),
        ("generator.yml", True),
        (".openapi-generator-ignore", True),
        ("README.md", False),
        ("generator.json", False),
        ("generator-Config.yaml", False),
    ],
)
def test_the_files_that_may_name_a_contract(name: str, expected: bool) -> None:
    from codetrail.system import _config_file

    assert _config_file(name) is expected
