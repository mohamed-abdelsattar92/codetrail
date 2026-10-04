"""The adr extractor: decisions and what supersedes what (design section 5.2)."""

from pathlib import Path

from codetrail.extract import run_extractors
from codetrail.extract.adr import AdrExtractor
from codetrail.facts import RelationKind

HAMESH_STYLE = """# 0023. Use httpx for the API's outbound calls

- Status: accepted (by the founder, 4 October 2026)
- Date: 2026-09-30
- Deciders: KoGy

## Context
Text.
"""

SUPERSEDED = """# 0005. Use requests

- Status: superseded by 0023
- Date: 2026-09-25
"""

NYGARD = """# 7. Record architecture decisions

Date: 2026-01-02

## Status

Accepted

## Context
"""


def run(root: Path, files: dict[str, str], globs: list[str] | None = None):  # type: ignore[no-untyped-def]
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return run_extractors(root, sorted(files), [AdrExtractor(globs or ["docs/adr/*.md"])])


def test_reads_hamesh_style_decisions(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"docs/adr/0023-httpx.md": HAMESH_STYLE})
    [decision] = extraction.entities
    assert decision.id == "decision:ADR-0023"
    assert dict(decision.attributes) == {
        "number": "0023",
        "title": "Use httpx for the API's outbound calls",
        "status": "accepted",
        "date": "2026-09-30",
        "path": "docs/adr/0023-httpx.md",
    }


def test_superseded_decisions_get_an_edge(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"docs/adr/0023-httpx.md": HAMESH_STYLE, "docs/adr/0005-requests.md": SUPERSEDED})
    statuses = {entity.id: entity.attributes["status"] for entity in extraction.entities}
    assert statuses["decision:ADR-0005"] == "superseded"
    assert [relation.key for relation in extraction.relations] == [
        ("decision:ADR-0023", str(RelationKind.SUPERSEDES), "decision:ADR-0005")
    ]


def test_reads_a_status_section(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"doc/adr/0007-record.md": NYGARD}, ["doc/adr/*.md"])
    [decision] = extraction.entities
    assert decision.id == "decision:ADR-0007"
    assert decision.attributes["status"] == "accepted"
    assert decision.attributes["date"] == "2026-01-02"
    assert decision.attributes["title"] == "Record architecture decisions"


def test_files_without_a_number_are_skipped(tmp_path: Path) -> None:
    extraction = run(
        tmp_path,
        {"docs/adr/README.md": "# Architecture decision records\n", "docs/adr/template.md": "# NNNN. Title\n"},
    )
    assert extraction.entities == []


def test_only_configured_paths_are_read(tmp_path: Path) -> None:
    extraction = run(tmp_path, {"docs/other/0001-x.md": HAMESH_STYLE})
    assert extraction.entities == []


def test_a_long_heading_line_is_read_in_linear_time(tmp_path: Path) -> None:
    import time

    hostile = "# 1 x" + " " * 200_000 + "y\n"
    started = time.perf_counter()
    run(tmp_path, {"docs/adr/0001-x.md": hostile})
    assert time.perf_counter() - started < 2
