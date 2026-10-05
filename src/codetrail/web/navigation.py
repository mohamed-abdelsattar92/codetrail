"""What the page's shell shows around every page (design section 16.1): the sidebar, a page's neighbours in its path,
and where to continue reading. Built from the guide's pages and the reader's learning states; malformed front matter
never breaks it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from codetrail.guide import Page

SIDEBAR_ANSWERS = 5


@dataclass(frozen=True)
class Navigation:
    paths: list[tuple[Page, int, int]] = field(default_factory=list)  # path, steps learned, steps
    areas: list[Page] = field(default_factory=list)
    concepts: list[Page] = field(default_factory=list)
    answers: list[Page] = field(default_factory=list)  # the newest few
    answer_count: int = 0
    statuses: Mapping[str, str] = field(default_factory=dict)  # page id -> learning state
    unread_digests: bool = False
    branch: str = ""
    commit: str = ""


def steps_of(path: Page) -> list[str]:
    steps = path.meta.get("steps")
    return [str(step) for step in steps] if isinstance(steps, list) else []


def _by_title(pages: Iterable[Page]) -> list[Page]:
    return sorted(pages, key=lambda page: (page.title.casefold(), page.id))


def answers_newest_first(pages: Iterable[Page]) -> list[Page]:
    """Saved answers, newest first by when they were asked; one with no usable date sorts last."""

    def asked(page: Page) -> str:
        value = page.meta.get("asked_at")
        return value if isinstance(value, str) else ""

    listed = list(pages)  # read once: callers may pass a generator
    dated = sorted((page for page in listed if asked(page)), key=asked, reverse=True)
    return dated + sorted((page for page in listed if not asked(page)), key=lambda page: page.id)


def build_navigation(
    pages: Sequence[Page], statuses: Mapping[str, str], unread_digests: Sequence[str], branch: str = "",
    commit: str = "",
) -> Navigation:  # fmt: skip
    paths = [
        (path, sum(1 for step in steps_of(path) if statuses.get(step) == "learned"), len(steps_of(path)))
        for path in _by_title(page for page in pages if page.kind == "path")
    ]
    answers = answers_newest_first(page for page in pages if page.kind == "answer")
    return Navigation(
        paths=paths,
        areas=_by_title(page for page in pages if page.kind == "area"),
        concepts=_by_title(page for page in pages if page.kind == "concept"),
        answers=answers[:SIDEBAR_ANSWERS],
        answer_count=len(answers),
        statuses=dict(statuses),
        unread_digests=bool(unread_digests),
        branch=branch,
        commit=commit,
    )


def path_neighbours(
    pages: Sequence[Page], page_id: str, path_hint: str | None
) -> tuple[Page | None, Page | None, Page | None]:
    """The path a page is read in, and its previous and next steps there.

    The path is the one the reader came from when it really holds the page, otherwise the first one that does (by
    title); a page in no path has none.
    """
    by_id = {page.id: page for page in pages}
    holding = [path for path in _by_title(page for page in pages if page.kind == "path") if page_id in steps_of(path)]
    if not holding:
        return None, None, None
    path = next((candidate for candidate in holding if candidate.id == path_hint), holding[0])
    steps = steps_of(path)
    position = steps.index(page_id)
    previous = by_id.get(steps[position - 1]) if position > 0 else None
    following = by_id.get(steps[position + 1]) if position + 1 < len(steps) else None
    return path, previous, following


def continue_reading(pages: Sequence[Page], statuses: Mapping[str, str]) -> tuple[Page, Page, int, int] | None:
    """The first path (by title) with a step not yet learned: the path, that step, steps learned, and steps."""
    by_id = {page.id: page for page in pages}
    for path in _by_title(page for page in pages if page.kind == "path"):
        steps = [step for step in steps_of(path) if step in by_id]
        learned = sum(1 for step in steps if statuses.get(step) == "learned")
        for step in steps:
            if statuses.get(step) != "learned":
                return path, by_id[step], learned, len(steps)
    return None
