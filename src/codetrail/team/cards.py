"""The team page's cards and overview (design section 24.7).

A repository has a card when it has something new: an unread digest, commits since its last update, or the last
catch-up failing it or the reader leaving it out (its free part caught its facts up, but not its guide). Cards come
busiest first (merges since the last update plus unread digests), then by name. A card shows its unread digests'
highlights, or an older digest's first paragraph, rendered by the guide's renderer with raw HTML off and under the
repository's prefix, so their links lead into its own guide. Who merged comes from git, as names only: nothing
here counts or ranks people.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from dataclasses import dataclass, field, replace

from codetrail.config import TARGET_NAME, Paths
from codetrail.database import connect
from codetrail.generate.validate import DIAGRAM
from codetrail.guide import COMMIT_ID, PAGE_ID, GuideRepository, Page
from codetrail.learn import LearningState
from codetrail.repo.history import authors_between
from codetrail.repo.signal import Signal
from codetrail.team.state import Overviews
from codetrail.web.links import BASE_PATH, site_path
from codetrail.web.render import Segment, render_body

BLANK_LINE = re.compile(r"\n[ \t]*\n")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Row:
    """One followed repository on the team page."""

    target: str
    repository: str  # the name the service gives it
    branch: str
    signal: Signal | None = None
    reason: str = ""  # why its signal couldn't be read, or why it isn't served
    served: bool = True  # its guide is mounted
    has_guide: bool = False  # its guide has pages: until then, its signal measures only the facts
    unread: list[Page] = field(default_factory=list)  # its unread digests, newest first
    authors: list[str] = field(default_factory=list)  # who merged what its card covers, as git records them

    @property
    def measured(self) -> bool:
        """Its signal was read and its guide is written, so it either has something new or nothing new."""
        signal = self.signal
        return (self.served and not self.reason and signal is not None and not signal.updating
                and signal.updated_commit is not None and self.has_guide)  # fmt: skip


@dataclass(frozen=True)
class DigestSummary:
    id: str
    title: str
    link: str
    highlights: list[list[Segment]]
    first_paragraph: list[Segment]  # shown by a digest from before highlights, which has none
    pages_changed: int


@dataclass(frozen=True)
class Card:
    row: Row
    digests: list[DigestSummary]
    authors: list[str]
    not_caught_up: bool  # commits landed since its last update, or the last catch-up missed it

    @property
    def busyness(self) -> int:
        """Merges since its last update plus unread digests: what orders the cards."""
        return (self.row.signal.merges if self.row.signal else 0) + len(self.digests)


def unread_digests(paths: Paths, target: str) -> list[Page]:
    """The target's unread digests, newest first. A target that was never updated has no database and no digest; a
    page view doesn't make one."""
    database = paths.target_data(target) / "codetrail.db"
    if not database.exists():
        return []
    digests = sorted(GuideRepository(paths.target_data(target) / "guide").pages("digest"),
                     key=lambda page: str(page.meta.get("written_at", "")), reverse=True)  # fmt: skip
    connection = connect(database)
    try:
        unread = set(LearningState(connection).unread_digests([page.id for page in digests]))
    finally:
        connection.close()
    return [page for page in digests if page.id in unread]


def card_authors(paths: Paths, target: str, signal: Signal, unread: list[Page]) -> list[str]:
    """Who merged what a card covers, up to the branch's head: from where its oldest unread digest with a start commit
    starts, or else from its last update. A digest with no start commit (the first guide's) covers no commits, so it
    names nobody. A repository with nothing new reads no git."""
    if signal.head is None or (not unread and not signal.commits):
        return []
    starts = [page.meta.get("from_commit") for page in reversed(unread)]  # oldest first
    start = next((since for since in starts if isinstance(since, str) and COMMIT_ID.fullmatch(since)),
                 signal.updated_commit)  # fmt: skip
    if start is None:
        return []
    return authors_between(paths.target_data(target) / "mirror.git", start, signal.head)


def build_cards(rows: list[Row], max_nodes: int, missed: Collection[str] = frozenset()) -> tuple[list[Card], list[Row]]:
    """The cards of the rows with something new, busiest first and then by name, and the other rows in order.
    `missed` are the targets the last catch-up failed or the reader left out of it."""
    cards: list[Card] = []
    rest: list[Row] = []
    for row in rows:
        signal = row.signal
        if row.measured and signal is not None and (signal.commits or row.unread or row.target in missed):
            cards.append(_card(row, signal, max_nodes, row.target in missed))
        else:
            rest.append(row)
    cards.sort(key=lambda card: (-card.busyness, card.row.repository.casefold()))
    return cards, rest


def _card(row: Row, signal: Signal, max_nodes: int, missed: bool) -> Card:
    return Card(row, digest_summaries(row.target, row.unread, max_nodes), row.authors, signal.commits > 0 or missed)


def digest_summaries(target: str, pages: list[Page], max_nodes: int) -> list[DigestSummary]:
    """The digests as a card shows them, linked into the target's own guide."""
    prefix = BASE_PATH.set(f"/repos/{target}")
    try:
        return [_summary(page, max_nodes) for page in pages]
    finally:
        BASE_PATH.reset(prefix)


def _summary(page: Page, max_nodes: int) -> DigestSummary:
    found = page.meta.get("highlights")
    lines = [line for line in found if isinstance(line, str)] if isinstance(found, list) else []
    highlights = [segments for segments in (render_body(line, None, max_nodes) for line in lines) if segments]
    # A digest from before highlights shows its first paragraph; the first guide's digest (no start commit) only lists
    # the new pages, so its title and page count say it all.
    first_guide = page.meta.get("from_commit") is None
    first = [] if highlights or first_guide else render_body(_first_paragraph(page.body), None, max_nodes)
    changed = page.meta.get("pages_changed")
    return DigestSummary(page.id, page.title, site_path(f"/pages/{page.id}"), highlights, first,
                         len(changed) if isinstance(changed, list) else 0)  # fmt: skip


def _first_paragraph(body: str) -> str:
    """The body's first block that isn't a heading or a diagram line."""
    for block in BLANK_LINE.split(body.strip()):
        first_line = block.strip().splitlines()[0] if block.strip() else ""
        if first_line and not first_line.startswith("#") and not DIAGRAM.match(first_line):
            return block
    return ""


def render_overview(body: str, cited: list[str]) -> list[Segment]:
    """The overview's segments. A documented block links to its source in the repository whose digest it was copied
    from (`cited`, one target per documented block, in order). Any other citation in an overview names no
    repository, so it has no link."""
    remaining = list(cited)
    segments = []
    for segment in render_body(body, None, 0):  # no diagram is drawn without a store, so no node limit applies
        if segment.kind == "rationale":
            target = remaining.pop(0) if segment.label == "documented" and remaining else None
            valid = isinstance(target, str) and TARGET_NAME.fullmatch(target) is not None and segment.link is not None
            segment = replace(segment, link=f"/repos/{target}{segment.link}" if valid else None)
        segments.append(segment)
    return segments


@dataclass(frozen=True)
class Overview:
    """An overview as a page shows it: its segments, and the digests it was written from."""

    id: str
    segments: list[Segment]
    digests: list[tuple[str, str, str]]  # each digest's target, repository and id


def load_overview(overviews: Overviews, overview_id: str, team: str) -> Overview | None:
    """The overview, rendered; None when it is missing, has an invalid id or can't be parsed."""
    try:
        found = overviews.read(overview_id)
    except Exception as error:  # an overview edited by hand mustn't stop the page
        logger.warning("Couldn't read team %r's overview: %s", team, type(error).__name__)
        return None
    if found is None:
        return None
    meta, body = found
    cited = meta.get("cited")
    listed = meta.get("digests")
    digests = [(str(digest.get("target")), str(digest.get("repository")), str(digest.get("id")))
               for digest in (listed if isinstance(listed, list) else []) if isinstance(digest, dict)]  # fmt: skip
    linked = [digest for digest in digests if TARGET_NAME.fullmatch(digest[0]) and PAGE_ID.fullmatch(digest[2])]
    segments = render_overview(body, [str(target) for target in cited] if isinstance(cited, list) else [])
    return Overview(overview_id, segments, linked)
