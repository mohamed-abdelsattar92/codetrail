"""Learning state: read, learned, stale, and back to learned (design section 8.3)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.guide import Page
from codetrail.learn import LearningState, check_hash, page_version

CHECKS = [
    {"id": "a", "question": "Why A?", "rubric": [{"point": "p", "grounds": ["x"]}]},
    {"id": "b", "question": "Why B?", "rubric": [{"point": "q", "grounds": ["y"]}]},
]


def page(body: str = "Body", checks: list[dict[str, object]] | None = None) -> Page:
    return Page("concepts/x", {"kind": "concept", "title": "X", "checks": CHECKS if checks is None else checks}, body)


@pytest.fixture
def learning(tmp_path: Path) -> LearningState:
    return LearningState(connect(tmp_path / "codetrail.db"))


def test_a_new_page_is_unread(learning: LearningState) -> None:
    assert learning.status(page()).state == "unread"


def test_marking_read(learning: LearningState) -> None:
    learning.mark_read(page())
    assert learning.status(page()).state == "read"


def test_passing_every_check_learns_the_page(learning: LearningState) -> None:
    current = page()
    learning.record_attempt(current, CHECKS[0], "answer", "pass", "Good.", "en", "c1")
    assert learning.status(current).state == "unread"
    learning.record_attempt(current, CHECKS[1], "answer", "fail", "Missed q.", "en", "c1")
    assert learning.status(current).passed == {"a"}
    learning.record_attempt(current, CHECKS[1], "answer", "pass", "Good.", "en", "c1")
    status = learning.status(current)
    assert status.state == "learned"
    assert status.learned_commit == "c1"


def test_a_rewritten_page_goes_stale_and_only_changed_checks_return(learning: LearningState) -> None:
    current = page()
    for check in CHECKS:
        learning.record_attempt(current, check, "a", "pass", "", "en", "c1")
    changed: dict[str, object] = {"id": "b", "question": "Why B, now?", "rubric": [{"point": "q2", "grounds": ["y"]}]}
    rewritten = page("New body", [CHECKS[0], changed])
    status = learning.status(rewritten)
    assert status.state == "stale"
    assert status.passed == {"a"}
    assert status.learned_commit == "c1"
    learning.record_attempt(rewritten, changed, "a", "pass", "", "en", "c2")
    assert learning.status(rewritten).state == "learned"


def test_a_page_without_checks_can_be_read_but_not_learned(learning: LearningState) -> None:
    bare = page(checks=[])
    learning.mark_read(bare)
    assert learning.status(bare).state == "read"


def test_partial_and_fail_are_not_passes(learning: LearningState) -> None:
    current = page()
    learning.record_attempt(current, CHECKS[0], "a", "partial", "", "en", "c1")
    assert learning.status(current).passed == set()


def test_versions_and_hashes_track_content() -> None:
    assert page_version(page()) == page_version(page())
    assert page_version(page()) != page_version(page("Other"))
    assert check_hash(CHECKS[0]) != check_hash({**CHECKS[0], "question": "Changed?"})


def test_digest_reads(learning: LearningState) -> None:
    assert learning.unread_digests(["digests/a", "digests/b"]) == ["digests/a", "digests/b"]
    learning.mark_digest_read("digests/a")
    assert learning.unread_digests(["digests/a", "digests/b"]) == ["digests/b"]


def test_learned_pages_are_listed(learning: LearningState) -> None:
    for check in CHECKS:
        learning.record_attempt(page(), check, "a", "pass", "", "en", "c1")
    assert learning.learned_page_ids() == {"concepts/x"}
