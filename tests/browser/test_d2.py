"""The system diagram drawn with D2's WebAssembly build, in its own worker, under the real policies (ADR 0016).

The page's policy never allows `eval`; the worker's does, and nothing else. Diagrams with hostile names are drawn
through the page's own module, and every box shows its label exactly as Codetrail wrote it.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind
from codetrail.facts.store import FactStore
from codetrail.web.diagrams import system_diagram
from tests.browser.conftest import Site
from tests.browser.test_mermaid import HOSTILE

pytestmark = pytest.mark.browser
DRAWING = 20_000  # ms: the first diagram compiles 22 MB of WebAssembly


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FactStore]:
    connection = connect(tmp_path / "hostile.db")
    store = FactStore(connection)
    kinds = ["service", "app", "library", "contract", "infrastructure", "platform"]
    parts = [
        Entity(
            f"part:sys/p{index}", EntityKind.PART, {"kind": kinds[index % 6], "name": name, "folder": f"sys/p{index}"}
        )
        for index, name in enumerate([*HOSTILE, 'x" {shape: image; icon: http://evil}', "${y} @import", "a\\"])
    ]
    relations = [
        Relation(
            parts[index].id,
            RelationKind.DEPENDS_ON,
            parts[index - 1].id,
            {"evidence": "matched" if index % 2 else "explicit"},
        )
        for index in range(1, len(parts))
    ]
    store.record("c1", parts, relations)
    yield store
    connection.close()


def test_the_system_page_draws_its_diagram_with_d2(site: Site, page: Page, errors: list[str]) -> None:
    site.sign_in(page, "/system")
    figure = page.locator('figure.diagram-block[data-language="d2"]').first
    image = figure.locator(".diagram-svg img")
    expect(image).to_be_visible(timeout=DRAWING)
    assert (image.get_attribute("src") or "").startswith("data:image/svg+xml")
    expect(figure.locator("pre.diagram")).to_be_hidden()
    assert errors == []


def test_every_label_comes_back_as_written(site: Site, page: Page, store: FactStore) -> None:
    site.sign_in(page, "/system")
    for focus, max_nodes in ((None, 100), (None, 4), ("sys/p3", 100)):
        source = system_diagram(store, focus, max_nodes).d2
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
        assert written and set(written) <= set(shown), (focus, max_nodes)
