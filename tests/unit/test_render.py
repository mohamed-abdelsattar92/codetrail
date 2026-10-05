"""Rendering a guide page: Markdown without raw HTML, rationale boxes, fact links and diagrams (design 6.4, 7.4)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Relation, RelationKind, Source
from codetrail.facts.store import FactStore
from codetrail.web.render import render_body


@pytest.fixture
def store(tmp_path: Path) -> FactStore:
    store = FactStore(connect(tmp_path / "codetrail.db"))
    store.record("c", [Entity("module:app/a.py", EntityKind.MODULE, {"name": "app.a"}, (Source("app/a.py"),))], [])
    return store


def html_of(body: str, store: FactStore) -> str:
    return "".join(segment.html for segment in render_body(body, store, 60) if segment.kind != "diagram")


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "[click](javascript:alert(1))",
        "[click](data:text/html;base64,PHNjcmlwdD4=)",
        '<a href="javascript:alert(1)">x</a>',
        "[x](vbscript:msgbox(1))",
    ],
)
def test_hostile_markdown_renders_inert(store: FactStore, hostile: str) -> None:
    html = html_of(hostile, store)
    assert "<script" not in html and "onerror=" not in html.replace("onerror=alert(1)&gt;", "")
    assert 'href="javascript:' not in html and 'href="data:' not in html and 'href="vbscript:' not in html


def test_ordinary_markdown_and_links(store: FactStore) -> None:
    html = html_of(
        "# Title\n\nSee [the docs](https://example.com/x) and [[module:app/a.py]].\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n",
        store,
    )
    assert "<h1>Title</h1>" in html
    assert 'href="https://example.com/x"' in html
    assert 'href="/facts/module:app/a.py"' in html
    assert "<table>" in html


def test_rationale_blocks_become_labelled_boxes(store: FactStore) -> None:
    segments = render_body(
        '> [!documented] docs/adr/0001-x.md#L3-L4\n> "We chose X."\n\n> [!inferred]\n> Probably Y.\n', store, 60
    )
    rationale = [segment for segment in segments if segment.kind == "rationale"]
    assert [(segment.label, segment.link) for segment in rationale] == [
        ("documented", "/source/docs/adr/0001-x.md#L3"),
        ("inferred", None),
    ]
    assert "We chose X." in rationale[0].html


def test_diagram_placeholders_become_diagrams(store: FactStore) -> None:
    segments = render_body("Intro.\n\n{{diagram imports scope=app}}\n\nAfter.\n", store, 60)
    assert [segment.kind for segment in segments] == ["html", "diagram", "html"]
    assert segments[1].diagram is not None and segments[1].diagram.mermaid.startswith("flowchart LR")


def test_a_diagram_that_would_draw_nothing_is_left_out(store: FactStore) -> None:
    segments = render_body("Intro.\n\n{{diagram imports scope=nowhere}}\n\nAfter.\n", store, 60)
    assert [segment.kind for segment in segments] == ["html", "html"]


def test_the_system_placeholder_draws_the_system_or_one_folder(store: FactStore) -> None:
    parts = [
        Entity("part:app", EntityKind.PART, {"kind": "service", "name": "api", "folder": "app"}),
        Entity("part:web", EntityKind.PART, {"kind": "app", "name": "site", "folder": "web"}),
    ]
    store.record("d", parts, [Relation("part:web", RelationKind.DEPENDS_ON, "part:app", {"evidence": "explicit"})])
    whole = render_body("{{diagram system}}\n", store, 60)
    focused = render_body("{{diagram system focus=web}}\n", store, 60)
    elsewhere = render_body("{{diagram system focus=docs}}\n", store, 60)
    assert [segment.kind for segment in whole] == ["diagram"] and [segment.kind for segment in focused] == ["diagram"]
    assert whole[0].diagram is not None and len(whole[0].diagram.nodes) == 2
    assert elsewhere == []
