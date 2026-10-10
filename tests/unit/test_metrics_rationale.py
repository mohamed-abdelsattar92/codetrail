"""The documented share of the guide's rationale, by source type and page (design section 18.1)."""

from pathspec import GitIgnoreSpec

from codetrail.guide import Page
from codetrail.metrics.rationale import citation_source, measure_rationale

ADR = GitIgnoreSpec.from_lines(["docs/adr/*.md"])
DOCUMENTS = GitIgnoreSpec.from_lines(["README*", "*.md", "docs/**"])
BODY = (
    '> [!documented] docs/adr/0001-x.md#L3-L3\n> "We chose Python."\n\n'
    '> [!documented] README.md#L1-L2\n> "A shop."\n\n'
    '> [!documented] app/db.py#L4\n> "Retries twice."\n\n'
    '> [!documented] commit:3f9c2e1\n> "Faster."\n\n'
    "> [!inferred]\n>\n> It reads well.\n> Second line.\n"
)


def page(page_id: str, kind: str, body: str, title: str | None = None) -> Page:
    return Page(page_id, {"kind": kind, "title": title or page_id}, body)


def test_blocks_are_counted_by_source_and_by_page() -> None:
    metric = measure_rationale(
        [
            page("concepts/x", "concept", "> [!inferred]\n> Guess.\n", "A concept"),
            page("areas/app", "area", BODY, "The app"),
            page("areas/lib", "area", "No rationale here.", "Library"),
            page("digests/2026-10-10-abc", "digest", BODY),
            page("answers/why", "answer", BODY),
        ],
        ADR,
        DOCUMENTS,
    )
    assert (metric.documented, metric.inferred) == (4, 2)
    assert metric.by_source == {"adr": 1, "docs": 1, "code": 1, "commit": 1}
    assert [(row.page.id, row.documented, row.inferred) for row in metric.pages] == [
        ("concepts/x", 0, 1),  # as many inferred as the app: by title
        ("areas/app", 4, 1),
        ("areas/lib", 0, 0),
    ]
    assert [(block.page.id, block.first_line) for block in metric.inferred_blocks] == [
        ("areas/app", "It reads well."),
        ("concepts/x", "Guess."),
    ]


def test_an_adr_that_also_matches_the_documents_is_an_adr() -> None:
    assert citation_source("docs/adr/0001-x.md#L3-L3", ADR, DOCUMENTS) == "adr"
    assert citation_source("docs/guide.md#L1", ADR, DOCUMENTS) == "docs"
    assert citation_source("services/api/README.md#L1", ADR, DOCUMENTS) == "docs"
    assert citation_source("src/app.py#L1-L9", ADR, DOCUMENTS) == "code"
    assert citation_source("commit:abc1234", ADR, DOCUMENTS) == "commit"


def test_a_page_without_usable_front_matter_isnt_counted() -> None:
    metric = measure_rationale([Page("areas/x", {}, BODY)], ADR, DOCUMENTS)
    assert (metric.documented, metric.inferred, metric.pages) == (0, 0, [])
