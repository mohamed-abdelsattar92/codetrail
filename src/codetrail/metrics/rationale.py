"""The documented share of the guide's rationale, by source type and by page (design section 18.1).

Only area and concept pages count: digests accumulate and are mostly about commits, and saved answers follow the
reader's questions rather than the repository.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pathspec import GitIgnoreSpec

from codetrail.generate.validate import parse_rationale
from codetrail.guide import Page

GUIDE_KINDS = ("area", "concept")
SOURCE_KINDS = ("adr", "docs", "code", "commit")


@dataclass(frozen=True)
class InferredBlock:
    page: Page
    first_line: str


@dataclass(frozen=True)
class PageRationale:
    page: Page
    documented: int
    inferred: int


@dataclass(frozen=True)
class RationaleMetric:
    documented: int
    inferred: int
    by_source: dict[str, int]
    pages: list[PageRationale]  # most inferred first, then by title
    inferred_blocks: list[InferredBlock]


def guide_pages(pages: Iterable[Page]) -> list[Page]:
    return [page for page in pages if page.kind in GUIDE_KINDS]


def citation_source(citation: str, adr: GitIgnoreSpec, documents: GitIgnoreSpec) -> str:
    """Where a documented block's quote comes from: a commit, an ADR, another document, or code (a comment)."""
    if citation.startswith("commit:"):
        return "commit"
    path = citation.split("#", 1)[0]
    if adr.match_file(path):
        return "adr"
    return "docs" if documents.match_file(path) else "code"


def measure_rationale(pages: Iterable[Page], adr: GitIgnoreSpec, documents: GitIgnoreSpec) -> RationaleMetric:
    by_source = dict.fromkeys(SOURCE_KINDS, 0)
    rows: list[PageRationale] = []
    inferred_blocks: list[InferredBlock] = []
    for page in guide_pages(pages):
        documented = inferred = 0
        for block in parse_rationale(page.body):
            if block.kind == "documented":
                documented += 1
                by_source[citation_source(block.citation or "", adr, documents)] += 1
            else:
                inferred += 1
                inferred_blocks.append(InferredBlock(page, next(iter(block.text.splitlines()), "")))
        rows.append(PageRationale(page, documented, inferred))
    rows.sort(key=lambda row: (-row.inferred, row.page.title.casefold(), row.page.id))
    inferred_blocks.sort(key=lambda block: block.page.id)  # stable: a page's blocks stay in order
    return RationaleMetric(sum(row.documented for row in rows), sum(row.inferred for row in rows), by_source, rows,
                           inferred_blocks)  # fmt: skip
