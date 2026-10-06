"""A revision's sections, put into the page they revise (design section 6.3)."""

from codetrail.generate.revise import apply_sections

PAGE = """Intro line.

## How it works

Old text.

### A detail

Old detail.

## Where it lives

```sh
# not a heading
```

In `app/`.
"""


def test_a_section_is_replaced_with_its_subsections() -> None:
    revised = apply_sections(PAGE, [{"heading": "## How it works", "body": "New text."}])
    assert revised == PAGE.replace("Old text.\n\n### A detail\n\nOld detail.", "New text.")


def test_a_subsection_can_be_replaced_alone() -> None:
    revised = apply_sections(PAGE, [{"heading": "### A detail", "body": "New detail."}])
    assert "Old text.\n\n### A detail\n\nNew detail.\n\n## Where it lives" in revised


def test_an_empty_body_removes_the_section() -> None:
    revised = apply_sections(PAGE, [{"heading": "## Where it lives", "body": ""}])
    assert revised == "Intro line.\n\n## How it works\n\nOld text.\n\n### A detail\n\nOld detail.\n"


def test_an_unknown_heading_is_added_at_the_end_and_a_repeated_heading_isnt_doubled() -> None:
    revised = apply_sections(PAGE, [{"heading": "## What changed", "body": "## What changed\n\nA new route."}])
    assert revised.endswith("In `app/`.\n\n## What changed\n\nA new route.\n")
    assert revised.count("## What changed") == 1


def test_a_heading_inside_a_code_fence_is_not_a_section() -> None:
    revised = apply_sections(PAGE, [{"heading": "# not a heading", "body": "x"}])
    assert revised.startswith(PAGE.rstrip("\n")) and revised.endswith("# not a heading\n\nx\n")  # added, not replaced


def test_no_sections_leave_the_page_as_it_was() -> None:
    assert apply_sections(PAGE, []) == PAGE


def test_removing_a_middle_section_leaves_one_blank_line() -> None:
    revised = apply_sections(PAGE, [{"heading": "### A detail", "body": ""}])
    assert "Old text.\n\n## Where it lives" in revised


def test_a_section_without_a_heading_is_ignored() -> None:
    assert apply_sections(PAGE, [{"heading": " ", "body": "Loose text."}]) == PAGE
