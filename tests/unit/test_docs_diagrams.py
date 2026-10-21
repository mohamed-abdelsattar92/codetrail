"""The README's and docs' diagrams are SVGs rendered from D2 sources by `just diagrams` (ADR 0016)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIAGRAMS = ROOT / "docs" / "diagrams"
DOCUMENTS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
DIAGRAM_IMAGE = re.compile(r"!\[[^\]]+\]\(([^)]*diagrams/[^)]+\.svg)\)")


def test_no_document_draws_with_mermaid() -> None:
    assert [document for document in DOCUMENTS if "```mermaid" in document.read_text()] == []


def test_every_source_has_its_generated_svg() -> None:
    sources = sorted(DIAGRAMS.glob("*.d2"))
    assert sources
    for source in sources:
        svg = source.with_suffix(".svg")
        marker = f"<!-- GENERATED from {source.relative_to(ROOT)} by just diagrams"
        assert svg.exists() and marker in svg.read_text()[:300], source.name


def test_every_diagram_a_document_shows_exists() -> None:
    shown = {
        (document.parent / match.group(1)).resolve()
        for document in DOCUMENTS
        for match in DIAGRAM_IMAGE.finditer(document.read_text())
    }
    assert shown == {source.with_suffix(".svg") for source in DIAGRAMS.glob("*.d2")}
