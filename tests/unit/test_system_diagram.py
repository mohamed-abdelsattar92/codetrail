"""The system diagram: parts and connections from facts, shaped by kind, explained arrow by arrow (design 17.4)."""

import re
from pathlib import Path
from urllib.parse import quote

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


def d2_ids(d2: str) -> dict[str, str]:
    """Label -> generated node id, for every D2 node line."""
    return {label: node for node, label in re.findall(r'^(n\d+): "((?:[^"\\]|\\.)*)"', d2, re.MULTILINE)}


def test_every_part_is_drawn_with_its_kinds_shape(store: FactStore) -> None:
    diagram = system_diagram(store, None, max_nodes=25)
    lines = diagram.d2.splitlines()
    assert lines[0] == "direction: right"
    ids = d2_ids(diagram.d2)
    assert f'{ids["api"]}: "api" {{style.border-radius: 8}}' in lines  # service: rounded
    assert f'{ids["site"]}: "site" {{style.border-radius: 8}}' in lines  # app: rounded
    assert f'{ids["ui"]}: "ui"' in lines  # library: plain
    assert f'{ids["openapi.yaml"]}: "openapi.yaml" {{shape: page}}' in lines  # contract: a document
    assert f'{ids["infra/app"]}: "infra/app" {{style.double-border: true}}' in lines  # infrastructure: double border
    assert f'{ids["Cloudflare"]}: "Cloudflare" {{style.border-radius: 999}}' in lines  # platform: a stadium
    assert set(ids.values()) == {node.id for node in diagram.nodes}
    assert {node.link for node in diagram.nodes} == {f"/facts/{quote(entity.id, safe='')}" for entity in PARTS}


def test_arrows_are_solid_when_explicit_and_dashed_when_matched(store: FactStore) -> None:
    lines = system_diagram(store, None, max_nodes=25).d2.splitlines()
    ids = d2_ids("\n".join(lines))
    assert f"{ids['site']} -> {ids['ui']}" in lines  # depends on: a plain arrow
    assert f'{ids["site"]} -> {ids["api"]}: "openapi.yaml"' in lines  # a client calls the service
    assert f'{ids["api"]} -> {ids["openapi.yaml"]}: "implements"' in lines
    assert f'{ids["site"]} -> {ids["Cloudflare"]}: "deployed on"' in lines
    assert f'{ids["api"]} -> {ids["Google Cloud"]}: "matched by name" {{style.stroke-dash: 3}}' in lines
    assert f'{ids["site"]} -> {ids["openapi.yaml"]}: "calls via"' not in lines  # drawn as the call


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
    ids = d2_ids(diagram.d2)
    assert f'{ids["site"]} -> {ids["packages/ (4 libraries)"]}: "4"' in diagram.d2.splitlines()
    assert diagram.rolled_up


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


def test_a_contract_shared_by_many_services_and_callers_draws_linearly(store: FactStore) -> None:
    import time

    from codetrail.web.diagrams import system_parts

    services = [part(f"s{index}/api", "service", f"api{index}") for index in range(2_000)]
    clients = [part(f"c{index}/web", "app", f"web{index}") for index in range(2_000)]
    contract = "part:contracts/openapi.yaml"
    evidence = {"evidence": "explicit"}
    relations = [
        *(Relation(f"part:s{index}/api", RelationKind.IMPLEMENTS, contract, evidence) for index in range(2_000)),
        *(Relation(f"part:c{index}/web", RelationKind.CALLS_VIA, contract, evidence) for index in range(2_000)),
    ]
    store.record("f", [*PARTS, *services, *clients], [*RELATIONS, *relations])
    started = time.monotonic()
    diagram = system_diagram(store, None, max_nodes=10_000)
    listed = dict(system_parts(store))
    assert time.monotonic() - started < 5
    assert len(diagram.d2.splitlines()) < 9_000  # each call is one arrow to the contract, not one per service
    assert all(len(row.called_by) <= 1 for row in listed["service"])


def test_the_roll_up_never_draws_more_than_the_limit(store: FactStore) -> None:
    services = [part(f"top{index}/api", "service", f"api{index}") for index in range(50)]
    store.record("g", [*PARTS, *services], RELATIONS)
    diagram = system_diagram(store, None, max_nodes=10)
    assert len(diagram.nodes) <= 10
    assert any(node.label.startswith("other parts (") for node in diagram.nodes)


def test_hostile_names_are_plain_d2_strings(store: FactStore) -> None:
    hostile = 'x" {shape: image; icon: http://evil}\n${y} @import \\'
    store.record("e", [part("apps/x", "app", hostile), *PARTS], RELATIONS)
    lines = system_diagram(store, None, max_nodes=25).d2.splitlines()
    [line] = [line for line in lines if "evil" in line]
    label = r'"x\\" \{shape: image; icon: http://evil\}\\\$\{y\} @import \\\\"'  # the line break dropped
    assert re.fullmatch(rf"n\d+: {label} \{{style.border-radius: 8\}}", line)


def test_every_d2_key_is_a_generated_id(store: FactStore) -> None:
    """D2's dagre layout writes edge ids into JavaScript it evaluates, escaping only backslashes and line breaks, so
    no name from the target may ever be a D2 key: only labels, inside quotes (ADR 0016, security review)."""
    hostile = "x`});fetch(1)//${y}"
    uses = explicit("apps/x", RelationKind.DEPENDS_ON, "packages/ui", "apps/x/package.json#L1")
    folders = [part(f'{hostile}"/lib{index}', "library", f"lib{index}") for index in range(3)]  # roll up by folder
    store.record("e", [part("apps/x", "app", hostile), *PARTS, *folders], [*RELATIONS, uses])
    for focus, max_nodes in ((None, 25), (None, 3), (None, 8), ("apps", 25)):
        lines = system_diagram(store, focus, max_nodes).d2.splitlines()
        assert lines[0] == "direction: right"
        for line in lines[1:]:
            assert re.fullmatch(r'n\d+(?: -> n\d+)?(?:: "(?:[^"\\]|\\.)*")?(?: \{[^"]*\}|: \{[^"]*\})?', line), line
