"""An update estimates its paid work after refreshing the facts, and asks before doing it (design 15.4)."""

from collections.abc import Callable
from pathlib import Path

import pytest

from codetrail.assistant import PageDraft, PageRequest, Usage
from codetrail.assistant.estimate import UpdateEstimate
from codetrail.assistant.fake import FakeAssistant
from codetrail.config import Paths
from codetrail.errors import CodetrailError
from codetrail.update import run_update
from tests.fixtures.repos import add_commit
from tests.integration.test_generation import PLAN, good_page, guide
from tests.integration.test_generation import paths as paths  # the fixture


def recorder(seen: list[UpdateEstimate], answer: bool) -> Callable[[UpdateEstimate], bool]:
    def confirm(estimate: UpdateEstimate) -> bool:
        seen.append(estimate)
        return answer

    return confirm


def kinds(estimate: UpdateEstimate) -> dict[str, tuple[int, int]]:
    return {line.call.kind: (line.expected, line.maximum) for line in estimate.lines}


def test_the_first_update_is_estimated_at_its_cap_and_can_be_declined(paths: Paths) -> None:
    seen: list[UpdateEstimate] = []
    claude = FakeAssistant(plans=[PLAN], page_writer=good_page)
    result = run_update(paths, "t", claude=claude, confirm=recorder(seen, False))
    assert result.declined and result.generation is None
    assert kinds(seen[0]) == {"plan": (1, 1), "write": (20, 40), "digest": (1, 1)}
    assert seen[0].lines[0].call.model == "claude-opus-5-5"
    assert seen[0].expected_usd is not None and seen[0].maximum_usd <= seen[0].budget_usd  # type: ignore[operator]
    assert claude.requests == []  # nothing was called
    assert guide(paths).read_outline() is None
    assert result.snapshot.id == 1  # the facts were recorded all the same


def test_a_later_update_estimates_only_whats_affected(paths: Paths, tmp_path: Path) -> None:
    run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=good_page), confirm=lambda estimate: True)
    seen: list[UpdateEstimate] = []
    run_update(paths, "t", claude=FakeAssistant(), confirm=recorder(seen, True))
    assert kinds(seen[0]) == {}  # nothing changed: no call at all
    add_commit(tmp_path / "target", {"services/api/app/cache.py": "from app import db\n"}, "feat(api): add a cache")
    seen.clear()
    claude = FakeAssistant(page_writer=good_page)
    run_update(paths, "t", claude=claude, confirm=recorder(seen, True))
    assert kinds(seen[0]) == {"write": (1, 2), "digest": (1, 1)}
    [page] = seen[0].pages
    assert (page.title, page.reason) == ("The API", "update") and page.changed > 0


def test_an_update_with_a_real_assistant_needs_a_confirmation(paths: Paths) -> None:
    with pytest.raises(CodetrailError, match="estimate"):
        run_update(paths, "t")


def test_the_token_budget_stops_an_update_whatever_the_price(paths: Paths) -> None:
    file = paths.target_file("t")
    file.write_text(file.read_text() + "[generation]\nmax_tokens_per_update = 1000\nconcurrency = 1\n")

    def unpriced_page(request: PageRequest) -> PageDraft:
        draft = good_page(request)
        return PageDraft(draft.body, draft.checks, draft.files_read, 0.0, Usage("codex", "unpriced", 900, 0, 200))

    result = run_update(paths, "t", claude=FakeAssistant(plans=[PLAN], page_writer=unpriced_page))
    assert result.generation is not None
    assert len(result.generation.written) == 1 and result.generation.left_for_later
