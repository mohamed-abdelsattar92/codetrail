"""The system diagram: parts and connections from facts, shaped by kind, explained arrow by arrow (design 17.4)."""

import re
from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore
from codetrail.web.diagrams import available_diagrams, system_diagram


def part(folder: str, kind: str, name: str) -> Entity:
    return Entity(f"part:{folder}", EntityKind.PART, {"kind": kind, "name": name, "folder": folder})


def explicit(source: str, kind: RelationKind, target: str, cited: str) -> Relation:
    path, line = cited.split("#L")
    return Relation(f"part:{source}", kind, f"part:{target}", {"evidence": "explicit", "source": cited},
                    (Source(path, int(line), int(line)),))  # fmt: skip


PARTS = [
    part("services/api", "service", "api"),
    part("apps/site", "app", "site"),
    part("packages/ui", "library", "ui"),
    part("contracts/openapi.yaml", "contract", "openapi.yaml"),
    part("infra/app", "infrastructure", "infra/app"),
    part("platform:cloudflare", "platform", "Cloudflare"),
    part("platform:google_cloud", "platform", "Google Cloud"),
]
RELATIONS = [
    explicit("apps/site", RelationKind.DEPENDS_ON, "packages/ui", "apps/site/package.json#L4"),
    explicit("services/api", RelationKind.IMPLEMENTS, "contracts/openapi.yaml", "services/api/Makefile#L2"),
    explicit("apps/site", RelationKind.CALLS_VIA, "contracts/openapi.yaml", "apps/site/package.json#L7"),
    explicit("apps/site", RelationKind.DEPLOYED_ON, "platform:cloudflare", ".github/workflows/deploy.yml#L9"),
    explicit("infra/app", RelationKind.DEPLOYED_ON, "platform:google_cloud", "infra/app/main.tf#L1"),
    Relation(
        "part:services/api",
        RelationKind.DEPLOYED_ON,
        "part:platform:google_cloud",
        {"evidence": "matched", "rule": "name", "names": ["api", "services/api"], "source": "infra/app/main.tf#L1"},
        (Source("infra/app/main.tf", 1, 1),),
    ),
]


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    store = FactStore(connect(tmp_path / "codetrail.db"))
    store.record("c", PARTS, RELATIONS)
    return store


def node_ids(mermaid: str) -> dict[str, str]:
    """Label -> generated node id, for every node line."""
    found = {}
    for line in mermaid.splitlines():
        match = re.match(r'\s+(n\d+)(?:\(\[|\[\[|\(|\[|@\{ shape: doc, label: )"([^"]*)"', line)
        if match:
            found[match.group(2)] = match.group(1)
    return found


def test_every_part_is_drawn_with_its_kinds_shape(store: FactStore) -> None:
    diagram = system_diagram(store, None, max_nodes=25)
    lines = diagram.mermaid.splitlines()
    assert lines[0] == "flowchart LR"
    assert any(re.fullmatch(r'\s+n\d+\("api"\)', line) for line in lines)  # service: rounded
    assert any(re.fullmatch(r'\s+n\d+\("site"\)', line) for line in lines)  # app: rounded
    assert any(re.fullmatch(r'\s+n\d+\["ui"\]', line) for line in lines)  # library: plain
    assert any(re.fullmatch(r'\s+n\d+@\{ shape: doc, label: "openapi.yaml" \}', line) for line in lines)
    assert any(re.fullmatch(r'\s+n\d+\[\["infra/app"\]\]', line) for line in lines)  # infrastructure: double border
    assert any(re.fullmatch(r'\s+n\d+\(\["Cloudflare"\]\)', line) for line in lines)  # platform: stadium
    assert {node.link for node in diagram.nodes} == {f"/facts/{entity.id}" for entity in PARTS}


def test_arrows_are_solid_when_explicit_and_dashed_when_matched(store: FactStore) -> None:
    diagram = system_diagram(store, None, max_nodes=25)
    ids = node_ids(diagram.mermaid)
    assert f"    {ids['site']} --> {ids['ui']}" in diagram.mermaid  # depends on: a plain arrow
    assert f'    {ids["site"]} -->|"openapi.yaml"| {ids["api"]}' in diagram.mermaid  # a client calls the service
    assert f'    {ids["api"]} -->|"implements"| {ids["openapi.yaml"]}' in diagram.mermaid
    assert f'    {ids["site"]} -->|"deployed on"| {ids["Cloudflare"]}' in diagram.mermaid
    assert f'    {ids["api"]} -.->|"matched by name"| {ids["Google Cloud"]}' in diagram.mermaid
    assert f'{ids["site"]} -->|"calls via"| {ids["openapi.yaml"]}' not in diagram.mermaid  # drawn as the call


def test_every_arrow_is_explained_with_its_evidence(store: FactStore) -> None:
    arrows = {(arrow.source, arrow.kind, arrow.target): arrow for arrow in system_diagram(store, None, 25).arrows}
    assert len(arrows) == len(RELATIONS)
    assert arrows[("site", "depends_on", "ui")].link == "/source/apps/site/package.json#L4"
    matched = arrows[("api", "deployed_on", "Google Cloud")]
    assert (matched.evidence, matched.names) == ("matched", ("api", "services/api"))


def test_a_focus_draws_one_part_and_its_neighbours(store: FactStore) -> None:
    diagram = system_diagram(store, "packages", max_nodes=25)
    assert {node.label for node in diagram.nodes} == {"ui", "site"}
    around_the_site = system_diagram(store, "apps/site", max_nodes=25)
    assert {node.label for node in around_the_site.nodes} == {"site", "ui", "api", "openapi.yaml", "Cloudflare"}
    assert system_diagram(store, "docs", max_nodes=25).nodes == []


def test_libraries_roll_up_above_the_limit(store: FactStore) -> None:
    extra = [part(f"packages/lib{index}", "library", f"lib{index}") for index in range(3)]
    uses = [Relation("part:apps/site", RelationKind.DEPENDS_ON, f"part:packages/lib{index}", {"evidence": "explicit"})
            for index in range(3)]  # fmt: skip
    store.record("d", [*PARTS, *extra], [*RELATIONS, *uses])
    diagram = system_diagram(store, None, max_nodes=8)
    labels = {node.label for node in diagram.nodes}
    assert "packages/ (4 libraries)" in labels
    assert {"api", "site", "openapi.yaml", "infra/app", "Cloudflare", "Google Cloud"} <= labels
    ids = node_ids(diagram.mermaid)
    assert f'    {ids["site"]} -->|"4"| {ids["packages/ (4 libraries)"]}' in diagram.mermaid
    assert diagram.rolled_up


def test_hostile_names_are_escaped_and_ids_generated(store: FactStore) -> None:
    store.record("e", [part("apps/x", "app", 'x"]) --> click n1 %%{init}%%'), *PARTS], RELATIONS)
    diagram = system_diagram(store, None, max_nodes=25)
    hostile = next(line for line in diagram.mermaid.splitlines() if "click" in line)
    assert re.fullmatch(r'\s+n\d+\("x#quot;#93;\) --#gt; click n1 #37;#37;#123;init#125;#37;#37;"\)', hostile)


def test_no_parts_draw_nothing(tmp_path: Path) -> None:
    empty = FactStore(connect(tmp_path / "empty.db"))
    assert system_diagram(empty, None, max_nodes=25).nodes == []
    assert not any("system" in placeholder for placeholder in available_diagrams(empty))


def test_the_system_diagram_is_offered_first(store: FactStore) -> None:
    assert available_diagrams(store)[0] == "{{diagram system}}"


def test_the_parts_are_listed_by_kind_with_their_connections(store: FactStore) -> None:
    from codetrail.web.diagrams import system_parts

    listed = dict(system_parts(store))
    assert list(listed) == ["service", "app", "library", "contract", "infrastructure", "platform"]
    api = listed["service"][0]
    assert (api.name, api.called_by, api.runs_on) == ("api", ["site"], ["Google Cloud"])
    site = listed["app"][0]
    assert (site.depends_on, site.runs_on) == (["ui"], ["Cloudflare"])
    assert listed["contract"][0].called_by == ["site"]
