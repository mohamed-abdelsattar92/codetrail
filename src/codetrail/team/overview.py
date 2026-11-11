"""A team's overview: one call with no tools that connects a catch-up's digests (design section 24.9).

The overview is validated like a page, for its own rules: nothing that looks like a secret, a word limit, documented
blocks copied exactly from the digests it was given, links only to those digests, and no fact links or diagrams. A
failed draft gets one retry with its problems; the overview is skipped with fewer than two digests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from markdown_it.token import Token

from codetrail.assistant import Assistant, AssistantError, OverviewDigest, OverviewDraft, OverviewRequest
from codetrail.assistant.prompts import digest_link
from codetrail.assistant.usage import UsageLog
from codetrail.config import GlobalConfig
from codetrail.generate.run import SharedBudget
from codetrail.generate.validate import DIAGRAM, FACT_LINK, FLAGGED_PROBLEM, normalize, parse_rationale
from codetrail.repo.secrets import SecretScanner
from codetrail.team.state import Overviews
from codetrail.web.render import MARKDOWN

BUDGET_SPENT = "The team's budget was spent before the overview."
ATTEMPTS = 2  # the draft and its one retry, as for a page (design section 6.5)


@dataclass(frozen=True)
class OverviewOutcome:
    overview_id: str | None
    problem: str | None


def validate_overview(
    body: str, digests: Sequence[OverviewDigest], max_words: int, scanner: SecretScanner
) -> tuple[list[str], list[str]]:
    """The overview's problems, and the target each of its documented blocks was copied from, in order."""
    if not body.strip():
        return ["The overview is empty."], []
    findings = scanner.scan_text(body)
    if findings:  # the other problems quote the draft, and they reach the retry prompt
        return [f"{FLAGGED_PROBLEM} ({finding.rule}); leave it out." for finding in findings], []
    problems: list[str] = []
    words = len(body.split())
    if words > max_words:
        problems.append(f"The overview has {words} words; keep it to at most {max_words}.")
    copied = _documented_blocks(digests)
    cited: list[str] = []
    for block in parse_rationale(body):
        if block.kind != "documented":
            continue
        target = copied.get((block.citation, normalize(block.text)))
        if target is None:
            problems.append(f"The documented block at line {block.line} isn't copied from a digest.")
        else:
            cited.append(target)
    links = {digest_link(digest.target, digest.digest_id) for digest in digests}
    for link in _links(MARKDOWN.parse(body)):
        if link not in links:
            problems.append(f"The link to {link} names no digest of this catch-up.")
    for fact in FACT_LINK.findall(body):
        problems.append(f"The fact link [[{fact}]] isn't allowed in an overview.")
    for number, line in enumerate(body.splitlines(), start=1):
        if DIAGRAM.match(line.strip()):
            problems.append(f"The diagram at line {number} isn't allowed in an overview.")
    return problems, cited


def _documented_blocks(digests: Sequence[OverviewDigest]) -> dict[tuple[str | None, str], str]:
    """Each documented block of the digests' bodies and highlights, by citation and text, with its digest's target."""
    blocks: dict[tuple[str | None, str], str] = {}
    for digest in digests:
        for text in (digest.body, *digest.highlights):
            for block in parse_rationale(text):
                if block.kind == "documented":
                    blocks.setdefault((block.citation, normalize(block.text)), digest.target)
    return blocks


def _links(tokens: Sequence[Token]) -> Iterator[str]:
    """Every link and image target markdown-it would render, however it is written (inline, reference, autolink)."""
    for token in tokens:
        if token.type == "link_open":
            yield str(token.attrGet("href"))
        elif token.type == "image":
            yield str(token.attrGet("src"))
        if token.children:
            yield from _links(token.children)


async def write_overview(
    team: str,
    digests: list[OverviewDigest],
    writer: Assistant,
    settings: GlobalConfig,
    scanner: SecretScanner,
    overviews: Overviews,
    usage: UsageLog | None,
    budget: SharedBudget | None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> OverviewOutcome:
    """Writes, checks and stores the overview of a catch-up's digests; a draft that fails gets one retry."""
    if len(digests) < 2:
        return OverviewOutcome(None, None)  # nothing to connect
    max_words = settings.team.max_overview_words
    problems: list[str] = []
    previous_body = ""
    for _attempt in range(ATTEMPTS):
        if budget is not None and budget.exhausted:
            return OverviewOutcome(None, BUDGET_SPENT)
        try:
            draft = await writer.write_overview(OverviewRequest(team, digests, max_words, problems, previous_body))
        except AssistantError as error:
            return OverviewOutcome(None, f"The overview couldn't be written: {error}")
        _spend(draft, usage, budget)
        problems, cited = validate_overview(draft.body, digests, max_words, scanner)
        if not problems:
            moment = now()
            overview_id = f"overviews/{moment.strftime('%Y-%m-%d-%H%M%S')}"
            meta: dict[str, object] = {
                "id": overview_id,
                "kind": "overview",
                "written_at": moment.isoformat(timespec="seconds"),
                "digests": [
                    {"target": digest.target, "repository": digest.repository, "id": digest.digest_id}
                    for digest in digests
                ],
                "cited": cited,
            }
            overviews.write(overview_id, meta, draft.body)
            return OverviewOutcome(overview_id, None)
        previous_body = draft.body
    return OverviewOutcome(None, "The overview failed its checks twice: " + "; ".join(problems[:3]))


def _spend(draft: OverviewDraft, usage: UsageLog | None, budget: SharedBudget | None) -> None:
    """Records the call's usage, and counts its cost against the team's budget, as an update's calls do."""
    cost = usage.record("overview", draft.usage) if usage is not None and draft.usage.provider else draft.cost_usd
    if budget is not None:
        budget.add(cost)
