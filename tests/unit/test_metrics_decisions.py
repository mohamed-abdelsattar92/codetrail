"""ADR health: statuses, ADRs waiting too long, and which ADRs the guide cites (design section 18.1)."""

from datetime import date, timedelta

from codetrail.facts import Entity, EntityKind
from codetrail.guide import Page
from codetrail.metrics.decisions import measure_decisions

TODAY = date(2026, 10, 10)


def decision(number: int, status: str | None, when: date | str | None = None) -> Entity:
    attributes: dict[str, str] = {
        "number": f"{number:04d}",
        "title": f"ADR {number}",
        "path": f"docs/adr/{number:04d}.md",
    }
    if status:
        attributes["status"] = status
    if when:
        attributes["date"] = str(when)
    return Entity(f"decision:ADR-{number:04d}", EntityKind.DECISION, attributes)


DECISIONS = [
    decision(1, "proposed", TODAY - timedelta(days=31)),
    decision(2, "proposed", TODAY - timedelta(days=30)),
    decision(3, "proposed"),
    decision(4, "deprecated", "2025-01-01"),
    decision(5, "superseded", "2025-01-01"),
    decision(6, "superseded", "2025-01-01"),
    decision(7, "superseded", "2025-01-01"),
    decision(8, "accepted", "2025-01-01"),
    decision(9, "accepted", "2025-01-01"),
    decision(10, "accepted", "2025-01-01"),
    decision(11, None),
    decision(12, "proposed", "2026-13-40"),
]
PAGES = [
    Page("areas/app", {"kind": "area", "title": "App"},
         '> [!documented] docs/adr/0005.md#L1-L2\n> "Old."\n\nSee [[decision:ADR-0004]] and [[decision:ADR-0008]].'),
    Page("concepts/c", {"kind": "concept", "title": "C", "facts": [{"id": "decision:ADR-0006", "hash": "x"}]}, ""),
    Page("digests/d", {"kind": "digest", "title": "D"}, "[[decision:ADR-0009]]"),
]  # fmt: skip


def ids(entities: list[Entity]) -> list[str]:
    return [entity.id.removeprefix("decision:ADR-") for entity in entities]


def test_adrs_needing_attention() -> None:
    metric = measure_decisions(DECISIONS, PAGES, TODAY, proposed_days=30)
    assert ids(metric.overdue) == ["0001"]
    assert ids(metric.undated) == ["0003", "0012"]
    assert ids(metric.superseded_cited) == ["0004", "0005", "0006"]  # a link, a quote, front matter
    assert ids(metric.uncited) == ["0009", "0010"]  # a digest doesn't count as citing
    assert metric.attention == 4


def test_adrs_are_grouped_by_status() -> None:
    metric = measure_decisions(DECISIONS, PAGES, TODAY, proposed_days=30)
    assert {status: len(found) for status, found in metric.by_status.items()} == {
        "proposed": 4, "deprecated": 1, "superseded": 3, "accepted": 3, "none": 1}  # fmt: skip
