"""The team page's cards (design section 24.7): who has something new, busiest first, each with its unread digests'
highlights, or an older digest's first paragraph, linked into its own guide."""

from codetrail.guide import Page
from codetrail.repo.signal import Signal
from codetrail.team.cards import Row, build_cards, digest_summaries, render_overview
from codetrail.web.links import BASE_PATH

MAX_NODES = 25
DOCUMENTED = '> [!documented] src/a.py#L1-L2\n> "Retry twice."'


def digest(digest_id: str, highlights: list[str] | None = None, body: str = "Things changed.") -> Page:
    meta: dict[str, object] = {"kind": "digest", "title": f"Changes {digest_id}", "from_commit": "a" * 40,
                               "to_commit": "b" * 40, "written_at": "2026-11-11T10:00:00+00:00",
                               "pages_changed": ["areas/app", "concepts/retries"]}  # fmt: skip
    if highlights is not None:
        meta["highlights"] = highlights
    return Page(f"digests/{digest_id}", meta, body)


def row(repository: str, merges: int = 0, commits: int = 0, unread: list[Page] | None = None, **fields: object) -> Row:
    signal = Signal("a" * 40, "b" * 40, merges, commits, ["services"] if commits else [])
    return Row(f"acme-{repository}", repository, "develop", signal, has_guide=True, unread=unread or [],
               **fields)  # type: ignore[arg-type]  # fmt: skip


def test_build_cards_busiest_first() -> None:
    rows = [
        row("web-shop", merges=1, commits=1, unread=[digest("2026-11-10-a")]),
        row("payments-api", merges=3, commits=4),
        row("Billing", unread=[digest("2026-11-09-a"), digest("2026-11-10-b")]),
        row("api-gateway", merges=2, commits=2),
    ]

    cards, rest = build_cards(rows, MAX_NODES)

    assert [card.row.repository for card in cards] == ["payments-api", "api-gateway", "Billing", "web-shop"]
    assert [card.busyness for card in cards] == [3, 2, 2, 2]
    assert [card.not_caught_up for card in cards] == [True, True, False, True]
    assert rest == []


def test_build_cards_nothing_new() -> None:
    quiet = row("docs-site")
    unreachable = Row("acme-inventory", "inventory", "develop", None, "git couldn't fetch the repository.")
    updating = Row("acme-ledger", "ledger", "develop", Signal(updating=True))

    cards, rest = build_cards([quiet, unreachable, updating, row("web-shop", merges=1, commits=1)], MAX_NODES)

    assert [card.row.repository for card in cards] == ["web-shop"]
    assert rest == [quiet, unreachable, updating]
    assert quiet.measured and not unreachable.measured and not updating.measured


def test_card_highlights_or_first_paragraph() -> None:
    old = digest("2026-11-01-a", body="## Payments\n\nCharges retry **twice** now.\n\nMore below.")
    new = digest("2026-11-10-a", highlights=["Charges retry twice.", DOCUMENTED])

    [card], _rest = build_cards([row("web-shop", unread=[new, old], authors=["alice", "Bob"])], MAX_NODES)

    first, second = card.digests
    assert (first.id, first.title, first.link) == ("digests/2026-11-10-a", "Changes 2026-11-10-a",
                                                   "/repos/acme-web-shop/pages/digests/2026-11-10-a")  # fmt: skip
    assert [[segment.kind for segment in highlight] for highlight in first.highlights] == [["html"], ["rationale"]]
    assert first.first_paragraph == [] and first.pages_changed == 2
    assert second.highlights == []
    [paragraph] = second.first_paragraph
    assert "<strong>twice</strong>" in paragraph.html and "Payments" not in paragraph.html
    assert "More below." not in paragraph.html
    assert card.authors == ["alice", "Bob"]


def test_card_citation_links_under_prefix() -> None:
    linked = digest("2026-11-10-a", highlights=[DOCUMENTED, "See [the retries](/pages/concepts/retries)."])

    [card], _rest = build_cards([row("web-shop", unread=[linked])], MAX_NODES)

    documented, plain = card.digests[0].highlights
    assert documented[0].link == "/repos/acme-web-shop/source/src/a.py#L1"
    assert 'href="/repos/acme-web-shop/pages/concepts/retries"' in plain[0].html
    assert BASE_PATH.get() == ""  # reset once the card is built


def test_render_overview_prefixes_cited() -> None:
    body = "\n\n".join([
        DOCUMENTED,
        "> [!inferred] src/b.py#L3\n> Both teams retry.",
        '> [!documented] src/c.py#L5\n> "Payments retry too."',
        "Read [the web-shop digest](/repos/acme-web-shop/pages/digests/2026-11-10-a).",
    ])  # fmt: skip

    segments = render_overview(body, ["acme-web-shop", "acme-payments-api"])

    rationale = [segment for segment in segments if segment.kind == "rationale"]
    assert [(segment.label, segment.link) for segment in rationale] == [
        ("documented", "/repos/acme-web-shop/source/src/a.py#L1"),
        ("inferred", None),  # its citation names no repository
        ("documented", "/repos/acme-payments-api/source/src/c.py#L5"),
    ]
    assert 'href="/repos/acme-web-shop/pages/digests/2026-11-10-a"' in segments[-1].html


def test_render_overview_never_links_outside_a_named_target() -> None:
    segments = render_overview(DOCUMENTED + "\n\n" + DOCUMENTED.replace("a.py", "b.py"), ["../elsewhere"])

    assert [segment.link for segment in segments] == [None, None]  # an invalid name, then no name at all


def test_render_overview_leaves_a_citation_of_an_unfollowed_target_unlinked() -> None:
    body = DOCUMENTED + "\n\n" + DOCUMENTED.replace("a.py", "c.py")

    segments = render_overview(body, ["acme-web-shop", "acme-payments-api"], {"acme-payments-api"})

    assert [segment.link for segment in segments] == [None, "/repos/acme-payments-api/source/src/c.py#L1"]


def test_a_repository_the_last_catch_up_missed_has_a_card() -> None:
    missed, quiet = row("web-shop"), row("docs-site")  # both caught up by their free part: no commits waiting

    cards, rest = build_cards([missed, quiet], MAX_NODES, missed={"acme-web-shop"})

    assert [(card.row.repository, card.not_caught_up) for card in cards] == [("web-shop", True)]
    assert rest == [quiet]


def test_a_first_guide_digest_shows_only_its_title() -> None:
    first = digest("2026-11-10-a", body="The guide's first pages:\n\n- [The app](/pages/areas/app)")
    first.meta["from_commit"] = None

    [card], _rest = build_cards([row("web-shop", unread=[first])], MAX_NODES)

    [summary] = card.digests
    assert summary.highlights == [] and summary.first_paragraph == [] and summary.pages_changed == 2


def test_digest_summaries_link_into_the_target_s_guide() -> None:
    summaries = digest_summaries("acme-web-shop", [digest("2026-11-10-a", ["Charges retry **twice**."])], MAX_NODES)

    assert [summary.link for summary in summaries] == ["/repos/acme-web-shop/pages/digests/2026-11-10-a"]
    assert summaries[0].pages_changed == 2
    assert BASE_PATH.get() == ""  # the prefix is put back
