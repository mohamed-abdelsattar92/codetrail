"""Guide pages turned into Markdown for coding agents (design section 23.2)."""

from codetrail.agents.convert import derived_brief, limit_words, to_agent_markdown
from codetrail.guide import Page


def test_rationale_becomes_one_marked_line() -> None:
    body = ('> [!documented] docs/adr/0007.md#L12-L18\n> "Use REST with the\n> OpenAPI document"\n\n'
            "> [!inferred]\n> The handlers stay thin.\n")  # fmt: skip
    assert to_agent_markdown(body) == (
        '[documented: docs/adr/0007.md#L12-L18] "Use REST with the OpenAPI document"\n\n'
        "[inferred] The handlers stay thin.\n"
    )


def test_a_commit_citation_keeps_its_mark() -> None:
    text = to_agent_markdown('> [!documented] commit:3f9c2e1\n> "Why: one way of working"\n')
    assert text == '[documented: commit:3f9c2e1] "Why: one way of working"\n'


def test_fact_links_and_diagrams_become_plain_text() -> None:
    text = to_agent_markdown("Uses [[module:app.db]].\n\n{{diagram imports scope=services/api}}\n")
    assert "`module:app.db`" in text and "[[" not in text
    assert "(diagram: imports scope=services/api; see the page in Codetrail)" in text
    assert "{{" not in text


def test_at_signs_are_neutralised() -> None:
    assert "@" not in to_agent_markdown("Read @docs/secret.md first.")


def test_a_derived_brief_keeps_marks_files_and_limit() -> None:
    body = "The API serves orders. It is small.\n\n## Why\n\n> [!inferred]\n> Thin handlers. Easy tests.\n"
    page = Page("areas/api", {"title": "The API", "files": [{"path": "app/main.py", "blob": "b"}]}, body)
    brief = to_agent_markdown(derived_brief(page, 300))
    assert brief.startswith("# The API") and "Key files: app/main.py" in brief
    assert "The API serves orders. It is small." in brief
    assert "[inferred] Thin handlers." in brief and "Easy tests" not in brief
    assert len(derived_brief(page, 5).split()) <= 5


def test_limit_words_cuts_and_counts() -> None:
    assert limit_words("a b c d e f", 3) == ("a b c", 3)
    assert limit_words("a b", 3) == ("a b", 0)
    assert limit_words("one two\nthree four\nfive", 4) == ("one two\nthree four", 1)
