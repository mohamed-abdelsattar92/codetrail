"""The team's overview: one call across a catch-up's digests, grounded in them and validated (design section 24.9)."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from codetrail.assistant import (
    AssistantError,
    OverviewDigest,
    OverviewDraft,
    OverviewRequest,
    PaidCallFailed,
    Usage,
)
from codetrail.assistant.fake import FakeAssistant
from codetrail.assistant.prompts import digest_link, overview_prompt
from codetrail.assistant.usage import UsageLog
from codetrail.config import GlobalConfig, ToolsSettings
from codetrail.database import connect
from codetrail.generate.run import SharedBudget
from codetrail.generate.validate import FLAGGED_PROBLEM
from codetrail.repo.secrets import SecretScanner
from codetrail.team.overview import OverviewOutcome, validate_overview, write_overview
from codetrail.team.state import Overviews
from codetrail.web.render import render_markdown
from tests.fixtures.repos import fake_github_token

SHOP_BLOCK = '> [!documented] commit:3f9c2e1\n> "Why: the payment calls timed out"'
QUEUE_BLOCK = '> [!documented] docs/adr/0003-queue.md#L1-L3\n> "Use a queue for payments"'
API_BLOCK = '> [!documented] commit:9a8b7c6\n> "Why: clients retried by hand"'
DIGESTS = [
    OverviewDigest(
        "shop",
        "acme/shop",
        "digests/2026-11-11-aaaaaaaaaaaa",
        "Checkout moved to a queue",
        f"Checkout now queues payments.\n\n{SHOP_BLOCK}\n",
        [QUEUE_BLOCK, "The worker is new."],
    ),
    OverviewDigest(
        "api", "acme/api", "digests/2026-11-11-bbbbbbbbbbbb", "Retries", f"The API retries.\n\n{API_BLOCK}\n", []
    ),
]
SHOP_LINK = "/repos/shop/pages/digests/2026-11-11-aaaaaaaaaaaa"
GOOD_BODY = (
    f"Payments and the API both changed how failures are handled.\n\n{SHOP_BLOCK}\n\n"
    "> [!inferred]\n> Both teams moved away from failing fast.\n\n"
    f"Read [the shop's digest]({SHOP_LINK}).\n"
)
NOW = datetime(2026, 11, 11, 10, 15, 0, tzinfo=UTC)


@pytest.fixture
def scanner() -> SecretScanner:
    return SecretScanner(ToolsSettings())


def check(body: str, scanner: SecretScanner, max_words: int = 250) -> tuple[list[str], list[str]]:
    return validate_overview(body, DIGESTS, max_words, scanner)


def test_validate_overview_accepts_a_grounded_overview(scanner: SecretScanner) -> None:
    assert check(GOOD_BODY, scanner) == ([], ["shop"])


def test_validate_overview_refuses_an_empty_body(scanner: SecretScanner) -> None:
    assert check(" \n", scanner) == (["The overview is empty."], [])


def test_validate_overview_secret_is_the_only_problem(scanner: SecretScanner) -> None:
    token = fake_github_token(21)
    body = f"The key is {token}.\n\n[[module:{token}]]\n\n[x](https://example.com/{token})\n"
    problems, _cited = check(body, scanner)
    assert problems and all(problem.startswith(FLAGGED_PROBLEM) for problem in problems)
    assert not any(token in problem for problem in problems)


def test_validate_overview_word_limit(scanner: SecretScanner) -> None:
    problems, _cited = check("word " * 51, scanner, max_words=50)
    assert problems == ["The overview has 51 words; keep it to at most 50."]
    assert check("word " * 50, scanner, max_words=50) == ([], [])


def test_validate_overview_documented_block_must_be_copied_from_a_digest(scanner: SecretScanner) -> None:
    whitespace = '> [!documented] commit:9a8b7c6\n> "Why:   clients\n> retried by hand"'
    body = f"Both changed.\n\n{QUEUE_BLOCK}\n\n{whitespace}\n"
    assert check(body, scanner) == ([], ["shop", "api"])  # a highlight's block counts; whitespace may differ
    altered_quote = '> [!documented] commit:3f9c2e1\n> "Why: the payment calls were slow"'
    altered_citation = '> [!documented] commit:9a8b7c6\n> "Why: the payment calls timed out"'
    problems, cited = check(f"Changed.\n\n{altered_quote}\n\n{altered_citation}\n", scanner)
    assert problems == [
        "The documented block at line 3 isn't copied from a digest.",
        "The documented block at line 6 isn't copied from a digest.",
    ]
    assert cited == []


@pytest.mark.parametrize(
    ("body", "link"),
    [
        ("See [this](https://example.com/news).", "https://example.com/news"),
        ("See [this](/repos/shop/pages/digests/2026-11-10-cccc).", "/repos/shop/pages/digests/2026-11-10-cccc"),
        (f"See [this]({SHOP_LINK.replace('shop', 'api')}).", SHOP_LINK.replace("shop", "api")),  # the wrong target
        ("See [this][ref].\n\n[ref]: https://example.com/ref", "https://example.com/ref"),
        ("See <https://example.com/auto>.", "https://example.com/auto"),
        ("See ![a chart](https://example.com/pixel.png).", "https://example.com/pixel.png"),
        ("See [this](//evil.example/x).", "//evil.example/x"),  # protocol-relative
        # An unclosed fence hides the rest from one parse, but the page renders each segment on its own.
        ("```\n> [!inferred]\n> Both changed.\n\nRead [it](https://evil.example/x).\n", "https://evil.example/x"),
        ("```\n> [!inferred]\n> Read [it](https://evil.example/y).\n", "https://evil.example/y"),
    ],
)
def test_validate_overview_links_only_to_its_digests(body: str, link: str, scanner: SecretScanner) -> None:
    assert check(body, scanner) == ([f"The link to {link} names no digest of this catch-up."], [])


def test_validate_overview_raw_html_link_renders_as_text(scanner: SecretScanner) -> None:
    body = 'See <a href="https://x">this</a>.'
    assert check(body, scanner) == ([], [])  # raw HTML is off: the page shows it as text
    assert '<a href="https://x"' not in render_markdown(body)


def test_validate_overview_links_to_each_digest(scanner: SecretScanner) -> None:
    body = "".join(f"- [{digest.title}]({digest_link(digest.target, digest.digest_id)})\n" for digest in DIGESTS)
    assert check(body, scanner) == ([], [])


def test_validate_overview_has_no_fact_links_or_diagrams(scanner: SecretScanner) -> None:
    problems, _cited = check("It touches [[module:app/main.py]].\n\n{{diagram system}}\n", scanner)
    assert problems == [
        "The fact link [[module:app/main.py]] isn't allowed in an overview.",
        "The diagram at line 3 isn't allowed in an overview.",
    ]


def test_digest_link() -> None:
    assert digest_link("shop", "digests/2026-11-11-aaaaaaaaaaaa") == SHOP_LINK


def fenced(prompt: str) -> list[tuple[str, str]]:
    return re.findall(r"<<<(data-[0-9a-f]{16})\n(.*?)\n\1>>>", prompt, flags=re.DOTALL)


def test_overview_prompt_fences_digests() -> None:
    prompt = overview_prompt(OverviewRequest("platform", DIGESTS, 250))
    blocks = [text for _boundary, text in fenced(prompt)]
    outside = re.sub(r"<<<(data-[0-9a-f]{16})\n.*?\n\1>>>", "", prompt, flags=re.DOTALL)
    for digest in DIGESTS:
        assert any(digest.body in block and digest.title in block for block in blocks)
        assert any(f"Repository: {digest.repository} (target {digest.target})" in block for block in blocks)
        assert digest.body.strip() not in outside and digest.title not in outside
        assert all(highlight in "".join(blocks) for highlight in digest.highlights)
        assert digest_link(digest.target, digest.digest_id) in outside
        assert digest.repository not in outside  # the service's name for it is data
    assert outside.count("platform") == 1  # the team's name, which validate_team_name keeps to a plain word
    assert "250 words" in prompt
    problem = "The link to https://evil.example/x names no digest of this catch-up."
    retry = overview_prompt(OverviewRequest("platform", DIGESTS, 250, [problem], "Old draft."))
    retry_blocks = [text for _boundary, text in fenced(retry)]
    assert f"- {problem}" in retry_blocks and "Old draft." in retry_blocks
    assert "evil.example" not in re.sub(r"<<<(data-[0-9a-f]{16})\n.*?\n\1>>>", "", retry, flags=re.DOTALL)


def usage(cost: float) -> Usage:
    return Usage("claude_code", "claude-sonnet-5-5", 1000, 0, 100, cost)


@pytest.fixture
def overviews(tmp_path: Path) -> Overviews:
    return Overviews(tmp_path / "team" / "overviews")


@pytest.fixture
def usage_log(tmp_path: Path) -> UsageLog:
    return UsageLog(connect(tmp_path / "team.db"), GlobalConfig().prices)


async def run(
    writer: FakeAssistant,
    scanner: SecretScanner,
    overviews: Overviews,
    usage_log: UsageLog | None,
    budget: SharedBudget | None,
    digests: list[OverviewDigest] = DIGESTS,
    cap_usd: float = 1.0,
) -> OverviewOutcome:
    return await write_overview(
        "platform", digests, writer, GlobalConfig(), scanner, overviews, usage_log, budget, cap_usd, now=lambda: NOW
    )


@pytest.mark.anyio
async def test_write_overview_skipped_below_two(
    scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog
) -> None:
    writer = FakeAssistant()
    assert await run(writer, scanner, overviews, usage_log, None, DIGESTS[:1]) == OverviewOutcome(None, None)
    assert writer.requests == [] and overviews.latest() is None


@pytest.mark.anyio
async def test_write_overview_retries_once_then_stores(
    scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog
) -> None:
    bad = "See [the news](https://example.com/news)."
    drafts: list[OverviewDraft | Exception] = [
        OverviewDraft(bad, 0.02, usage(0.02)),
        OverviewDraft(GOOD_BODY, 0.03, usage(0.03)),
    ]
    writer = FakeAssistant(overviews=drafts)
    budget = SharedBudget(5.0)
    outcome = await run(writer, scanner, overviews, usage_log, budget)
    assert outcome == OverviewOutcome("overviews/2026-11-11-101500", None)
    first, second = writer.requests
    assert isinstance(first, OverviewRequest) and isinstance(second, OverviewRequest)
    assert (first.team, first.digests, first.max_words, first.problems) == ("platform", DIGESTS, 250, [])
    assert second.problems == ["The link to https://example.com/news names no digest of this catch-up."]
    assert second.previous_body == bad
    stored = overviews.read("overviews/2026-11-11-101500")
    assert stored is not None
    meta, body = stored
    assert body == GOOD_BODY.strip()
    assert meta == {
        "id": "overviews/2026-11-11-101500",
        "kind": "overview",
        "written_at": "2026-11-11T10:15:00+00:00",
        "digests": [
            {"target": "shop", "repository": "acme/shop", "id": "digests/2026-11-11-aaaaaaaaaaaa"},
            {"target": "api", "repository": "acme/api", "id": "digests/2026-11-11-bbbbbbbbbbbb"},
        ],
        "cited": ["shop"],
    }
    rows = usage_log.connection.execute("SELECT kind, cost_usd FROM assistant_calls ORDER BY id").fetchall()
    assert [(row[0], row[1]) for row in rows] == [("overview", 0.02), ("overview", 0.03)]
    assert budget.spent_usd == pytest.approx(0.05)


@pytest.mark.anyio
async def test_write_overview_without_a_usage_log_counts_the_drafts_cost(
    scanner: SecretScanner, overviews: Overviews
) -> None:
    budget = SharedBudget(5.0)
    writer = FakeAssistant(overviews=[OverviewDraft(GOOD_BODY, 0.04, usage(0.04))])
    assert (await run(writer, scanner, overviews, None, budget)).overview_id == "overviews/2026-11-11-101500"
    assert budget.spent_usd == pytest.approx(0.04)


@pytest.mark.anyio
async def test_write_overview_fails_twice(scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog) -> None:
    bad = "[[module:a.py]] [a](https://a.example) [b](https://b.example) [c](https://c.example)"
    writer = FakeAssistant(overviews=[OverviewDraft(bad), OverviewDraft(bad)])
    outcome = await run(writer, scanner, overviews, usage_log, SharedBudget(5.0))
    assert outcome == OverviewOutcome(
        None,
        "The overview failed its checks twice: The link to https://a.example names no digest of this catch-up.; "
        "The link to https://b.example names no digest of this catch-up.; "
        "The link to https://c.example names no digest of this catch-up.",
    )
    assert len(writer.requests) == 2 and overviews.latest() is None


@pytest.mark.anyio
async def test_write_overview_budget_spent(scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog) -> None:
    budget = SharedBudget(1.0)
    assert budget.reserve(1.0)
    budget.settle(1.0, 1.0)
    writer = FakeAssistant()
    outcome = await run(writer, scanner, overviews, usage_log, budget)
    assert outcome == OverviewOutcome(None, "The team's budget was spent before the overview.")
    assert writer.requests == []


@pytest.mark.anyio
async def test_write_overview_retry_stops_at_the_budget(
    scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog
) -> None:
    writer = FakeAssistant(overviews=[OverviewDraft("[a](https://a.example)", 1.0, usage(1.0))])
    outcome = await run(writer, scanner, overviews, usage_log, SharedBudget(1.0))
    assert outcome == OverviewOutcome(None, "The team's budget was spent before the overview.")
    assert len(writer.requests) == 1


@pytest.mark.anyio
async def test_write_overview_assistant_error_is_a_problem(
    scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog
) -> None:
    writer = FakeAssistant(overviews=[AssistantError("Claude Code ended without a result.")])
    outcome = await run(writer, scanner, overviews, usage_log, None)
    assert outcome == OverviewOutcome(None, "The overview couldn't be written: Claude Code ended without a result.")


@pytest.mark.anyio
async def test_a_failed_overview_still_counts_what_it_cost(
    scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog
) -> None:
    failed = PaidCallFailed("Claude Code didn't finish the task (error_max_turns).", usage(0.3))
    writer = FakeAssistant(overviews=[failed])
    budget = SharedBudget(5.0)
    outcome = await run(writer, scanner, overviews, usage_log, budget)
    assert outcome.overview_id is None and "error_max_turns" in str(outcome.problem)
    rows = usage_log.connection.execute("SELECT kind, cost_usd FROM assistant_calls").fetchall()
    assert [(row[0], row[1]) for row in rows] == [("overview", 0.3)]
    assert budget.spent_usd == pytest.approx(0.3)


@pytest.mark.anyio
async def test_fake_writes_a_plain_overview_when_none_is_scripted() -> None:
    writer = FakeAssistant()
    draft = await writer.write_overview(OverviewRequest("platform", DIGESTS, 250))
    assert draft == OverviewDraft("Across the team, things changed.")
    assert len(writer.requests) == 1


@pytest.mark.anyio
async def test_the_overview_starts_only_when_its_cap_fits_in_the_teams_budget(
    scanner: SecretScanner, overviews: Overviews, usage_log: UsageLog
) -> None:
    """The team's budget isn't spent, but what is left can't hold the call's cap: it doesn't start (design 24.8)."""
    budget = SharedBudget(1.5)
    assert budget.reserve(1.0)
    budget.settle(1.0, 0.6)
    writer = FakeAssistant()
    outcome = await run(writer, scanner, overviews, usage_log, budget)
    assert outcome == OverviewOutcome(None, "The team's budget was spent before the overview.")
    assert writer.requests == []
    assert (await run(writer, scanner, overviews, usage_log, budget, cap_usd=0.5)).overview_id is not None
