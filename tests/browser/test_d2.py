"""Every diagram drawn with D2's WebAssembly build, in its own worker, under the real policies (design 4.4, ADR 0016).

The page's policy never allows `eval`; the worker's does, and nothing else. Every diagram kind, built from facts with
hostile names, is drawn through the page's own module, and every box shows its label exactly as Codetrail wrote it.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore
from codetrail.web.diagrams import Diagram, dependencies_diagram, imports_diagram, resources_diagram, system_diagram
from tests.browser.conftest import Site

pytestmark = pytest.mark.browser
DRAWING = 20_000  # ms: the first diagram compiles 22 MB of WebAssembly

HOSTILE = ['x" {shape: image; icon: http://evil}', "${y} @import", "a\\", "x`});fetch(1)//",
           'a"b', "a<b>c", "a#b", "a%%b", "a`b", "a[b]", "a{b}", "a|b", "a;b", "a\\b", "a&b", "end", "graph", "x-->y",
           "click n1 call alert(1)", "%%{init: {}}%%", "naïve ünïcödé", "😀 emoji", "", "   ",
           "a\x01b", "a\x7fb", "a\uffffb"]  # fmt: skip


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FactStore]:
    connection = connect(tmp_path / "codetrail.db")
    store = FactStore(connection)
    entities: list[Entity] = []
    relations: list[Relation] = []
    for index, name in enumerate(HOSTILE):
        path = f"app/pkg{index}/m{index}.py"
        entities.append(Entity(f"module:{path}", EntityKind.MODULE, {"name": name}, (Source(path),)))
        if index:
            relations.append(Relation(f"module:{path}", RelationKind.IMPORTS, "module:app/pkg0/m0.py"))
    swift = Entity("project:apps/ios/Packages/APIClient", EntityKind.PROJECT, {"name": "APIClient"})
    python = Entity("project:services/api", EntityKind.PROJECT, {"name": 'api "quoted"'})
    packages = [
        Entity("package:swift/swift-openapi-runtime", EntityKind.PACKAGE),
        Entity("package:swift/grdb.swift", EntityKind.PACKAGE),
        Entity("package:pypi/fastapi", EntityKind.PACKAGE),
    ]
    relations += [
        Relation(swift.id, RelationKind.DEPENDS_ON, "package:swift/swift-openapi-runtime", {}),
        Relation(swift.id, RelationKind.DEPENDS_ON, "package:swift/grdb.swift", {"group": ""}),
        Relation(python.id, RelationKind.DEPENDS_ON, "package:pypi/fastapi", {"group": 'main|"x"'}),
    ]
    terraform = [
        Entity("terraform_module:infra", EntityKind.TERRAFORM_MODULE, {}, (Source("infra/main.tf"),)),
        Entity("resource:infra/aws_s3_bucket.notes", EntityKind.RESOURCE, {"module": "infra"}, (Source("infra/s.tf"),)),
    ]
    relations.append(Relation("terraform_module:infra", RelationKind.CONTAINS, "resource:infra/aws_s3_bucket.notes"))
    kinds = ["service", "app", "library", "contract", "infrastructure", "platform"]
    parts = [
        Entity(
            f"part:sys/p{index}", EntityKind.PART, {"kind": kinds[index % 6], "name": name, "folder": f"sys/p{index}"}
        )
        for index, name in enumerate(HOSTILE)
    ]
    for index in range(1, len(HOSTILE)):
        kind = [RelationKind.DEPENDS_ON, RelationKind.IMPLEMENTS, RelationKind.CALLS_VIA, RelationKind.DEPLOYED_ON][
            index % 4
        ]
        evidence = "matched" if index % 3 == 0 else "explicit"
        relations.append(Relation(f"part:sys/p{index}", kind, f"part:sys/p{index - 1}", {"evidence": evidence}))
    relations.append(Relation("part:sys/p1", RelationKind.CALLS_VIA, "part:sys/p3", {"evidence": "explicit"}))
    relations.append(Relation("part:sys/p0", RelationKind.IMPLEMENTS, "part:sys/p3", {"evidence": "explicit"}))
    store.record("c1", [*entities, swift, python, *packages, *terraform, *parts], relations)
    yield store
    connection.close()


def all_diagrams(store: FactStore) -> list[tuple[str, Diagram]]:
    return [
        ("imports", imports_diagram(store, "app", max_nodes=100)),
        ("imports rolled up", imports_diagram(store, "app", max_nodes=3)),
        ("swift dependencies without groups", dependencies_diagram(store, "project:apps/ios/Packages/APIClient")),
        ("python dependencies with a hostile group", dependencies_diagram(store, "project:services/api")),
        ("resources", resources_diagram(store, "infra", max_nodes=100)),
        ("system with every shape and dashed, labelled arrows", system_diagram(store, None, max_nodes=100)),
        ("system rolled up", system_diagram(store, None, max_nodes=4)),
        ("system focused", system_diagram(store, "sys/p3", max_nodes=100)),
    ]


def test_the_system_page_draws_its_diagram_with_d2(site: Site, page: Page, errors: list[str]) -> None:
    site.sign_in(page, "/system")
    figure = page.locator("figure.diagram-block").first
    image = figure.locator(".diagram-svg img")
    expect(image).to_be_visible(timeout=DRAWING)
    assert (image.get_attribute("src") or "").startswith("data:image/svg+xml")
    expect(figure.locator("pre.diagram")).to_be_hidden()
    assert errors == []


def test_every_diagram_draws_and_every_label_comes_back_as_written(site: Site, page: Page, store: FactStore) -> None:
    site.sign_in(page, "/system")
    for name, diagram in all_diagrams(store):
        source = diagram.d2
        written = [re.sub(r"\\(.)", r"\1", label) for label in re.findall(r'^n\d+: "((?:[^"\\]|\\.)*)"', source, re.M)]
        shown = page.evaluate(
            """async (source) => {
                const { renderD2 } = await import("/static/js/d2.js");
                const svg = await renderD2(source, false);
                const drawing = new DOMParser().parseFromString(svg, "image/svg+xml");
                return [...drawing.querySelectorAll("text")].map((text) => text.textContent);
            }""",
            source,
        )
        assert diagram.nodes and written and set(written) <= set(shown), name
