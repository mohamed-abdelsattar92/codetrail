"""ADR health: statuses, proposed ADRs waiting too long, and which ADRs the guide cites (design section 18.1).

A page cites an ADR with a documented block quoting its file, a `[[decision:…]]` link, or the ADR in its front
matter's `facts`. Only area and concept pages count, as for the documented share.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from codetrail.facts import Entity
from codetrail.generate.validate import FACT_LINK, parse_rationale
from codetrail.guide import Page
from codetrail.metrics.rationale import guide_pages

RETIRED = ("superseded", "deprecated")


@dataclass(frozen=True)
class DecisionMetric:
    by_status: dict[str, list[Entity]]  # "none" when an ADR states no status
    overdue: list[Entity]
    undated: list[Entity]  # proposed, with no usable date
    superseded_cited: list[Entity]  # superseded or deprecated, still cited
    uncited: list[Entity]  # accepted, cited by no page

    @property
    def attention(self) -> int:
        return len(self.overdue) + len(self.superseded_cited)


def cited_decisions(pages: Iterable[Page], decisions: Iterable[Entity]) -> set[str]:
    by_path = {str(decision.attributes.get("path", "")): decision.id for decision in decisions}
    cited: set[str] = set()
    for page in guide_pages(pages):
        cited.update(FACT_LINK.findall(page.body))
        for block in parse_rationale(page.body):
            if block.kind == "documented" and block.citation:
                cited.add(by_path.get(block.citation.split("#", 1)[0], ""))
        facts = page.meta.get("facts")
        for fact in facts if isinstance(facts, list) else []:
            if isinstance(fact, dict):
                cited.add(str(fact.get("id", "")))
    return cited


def _date(decision: Entity) -> date | None:
    try:
        return date.fromisoformat(str(decision.attributes.get("date", "")))
    except ValueError:
        return None


def measure_decisions(
    decisions: Iterable[Entity], pages: Iterable[Page], today: date, proposed_days: int
) -> DecisionMetric:
    found = sorted(decisions, key=lambda decision: decision.id)
    cited = cited_decisions(pages, found)
    by_status: dict[str, list[Entity]] = {}
    overdue, undated, superseded_cited, uncited = [], [], [], []
    for decision in found:
        status = str(decision.attributes.get("status") or "none")
        by_status.setdefault(status, []).append(decision)
        if status == "proposed":
            written = _date(decision)
            if written is None:
                undated.append(decision)
            elif (today - written).days > proposed_days:
                overdue.append(decision)
        elif status in RETIRED and decision.id in cited:
            superseded_cited.append(decision)
        elif status == "accepted" and decision.id not in cited:
            uncited.append(decision)
    return DecisionMetric(by_status, overdue, undated, superseded_cited, uncited)
