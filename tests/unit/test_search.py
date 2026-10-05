"""Search over the guide: queries kept literal, ranking, snippets, and what is indexed (design section 16.3)."""

from pathlib import Path

import pytest

from codetrail.database import connect
from codetrail.facts import Entity, EntityKind, Source
from codetrail.facts.store import FactStore
from codetrail.guide import GuideRepository, Page
from codetrail.search import Document, SearchIndex, guide_documents, match_expression

HOSTILE = [
    "title:secret",
    '"',
    '""',
    "NEAR(a b)",
    "a OR b",
    "a AND NOT b",
    "-x",
    "*",
    "^x",
    "(a",
    "a)",
    "it's",
    "'; DROP TABLE documents; --",
    "🔍",
    "retry 🔍",
    "x" * 500,
    "{col1 col2}: x",
    "a + b",
    "\\",
    "\x00",
]


def document(identifier: str, title: str, text: str = "", headings: str = "", kind: str = "concept") -> Document:
    return Document(identifier, kind, title, headings, text, f"/pages/{identifier}")


def test_words_are_quoted_and_the_last_matches_as_a_prefix() -> None:
    assert match_expression("payment retr", 200) == '"payment" "retr"*'


def test_quotes_inside_a_word_are_doubled() -> None:
    assert match_expression('say"hi', 200) == '"say""hi"*'


@pytest.mark.parametrize("query", ["NEAR(a b)", "a OR b", "title:x", "-x", "^x"])
def test_operators_stay_literal_text(query: str) -> None:
    expression = match_expression(query, 200)
    assert expression is not None
    for word in expression.removesuffix("*").split(" "):
        assert word.startswith('"') and word.endswith('"')


def test_the_query_is_cut_to_its_limit() -> None:
    assert match_expression("abcdef ghij", 4) == '"abcd"*'


@pytest.mark.parametrize("query", ["", "   ", "-- ** ''", "🔍"])
def test_a_query_without_words_gives_nothing(query: str) -> None:
    assert match_expression(query, 200) is None


def test_non_latin_words_are_kept() -> None:
    assert match_expression("الدفع", 200) == '"الدفع"*'


@pytest.fixture
def index() -> SearchIndex:
    index = SearchIndex()
    index.rebuild(
        [
            document("concepts/retry", "Payment retries", "A failed charge goes back on the queue."),
            document("concepts/queue", "Queues", "The queue retries a payment after a delay.", "Delays"),
            document("concepts/ledger", "Ledger", "Writes every 🔍 entry; then it retries nothing."),
        ]
    )
    return index


def test_a_title_match_ranks_above_a_body_match(index: SearchIndex) -> None:
    results = index.search("retries", 200, 10)
    assert results[0].document.id == "concepts/retry"
    assert {result.document.id for result in results} == {"concepts/retry", "concepts/queue", "concepts/ledger"}


def test_a_prefix_finds_the_word(index: SearchIndex) -> None:
    assert [result.document.id for result in index.search("ledg", 200, 10)] == ["concepts/ledger"]


def test_a_heading_match_is_found(index: SearchIndex) -> None:
    assert [result.document.id for result in index.search("delays", 200, 10)] == ["concepts/queue"]


def test_the_limit_is_kept(index: SearchIndex) -> None:
    assert len(index.search("retries", 200, 2)) == 2


@pytest.mark.parametrize("query", HOSTILE)
def test_hostile_queries_never_raise(index: SearchIndex, query: str) -> None:
    assert isinstance(index.search(query, 200, 10), list)


def test_rebuild_replaces_everything(index: SearchIndex) -> None:
    index.rebuild([document("concepts/other", "Other")])
    assert index.search("payment", 200, 10) == []
    assert len(index.search("other", 200, 10)) == 1


def test_add_replaces_a_document_with_the_same_id(index: SearchIndex) -> None:
    index.add(document("concepts/retry", "Renamed"))
    assert [result.document.title for result in index.search("renamed", 200, 10)] == ["Renamed"]
    assert all(result.document.title != "Payment retries" for result in index.search("payment", 200, 10))


def test_snippets_are_plain_segments_marking_the_matched_words(index: SearchIndex) -> None:
    [result] = index.search("ledger entry", 200, 10)
    text = "".join(piece for piece, _ in result.snippet)
    assert text in "Writes every 🔍 entry; then it retries nothing."
    assert [piece for piece, matched in result.snippet if matched] == ["entry"]


def test_a_snippet_centres_on_a_match_far_into_the_text() -> None:
    index = SearchIndex()
    index.rebuild([document("concepts/long", "Long", "filler " * 200 + "needle at the end")])
    [result] = index.search("needle", 200, 10)
    assert [piece for piece, matched in result.snippet if matched] == ["needle"]
    assert len("".join(piece for piece, _ in result.snippet)) <= 200


def test_markup_in_a_document_stays_text_in_its_snippet() -> None:
    index = SearchIndex()
    index.rebuild([document("concepts/x", "X", "uses <script>alert(1)</script> here")])
    [result] = index.search("script", 200, 10)
    assert "<script>" in "".join(piece for piece, _ in result.snippet)


def write(guide: GuideRepository, page_id: str, meta: dict[str, object], body: str) -> None:
    guide.write_page(Page(page_id, meta, body))


def test_guide_documents_read_titles_and_bodies_only(tmp_path: Path) -> None:
    guide = GuideRepository(tmp_path / "guide")
    write(
        guide, "concepts/retry",
        {"kind": "concept", "title": "Payment retries", "checks": [{"id": "q", "question": "Why?",
                                                                   "rubric": [{"point": "zanzibar"}]}]},
        "## How it works\n\nA failed charge is **retried**, see [[module:app/retry.py]].\n\n"
        "> [!documented] docs/adr/0001.md#L1-L2\n> \"We retry.\"\n\n{{diagram imports scope=app}}\n",
    )  # fmt: skip
    write(
        guide,
        "answers/2026-10-05-why-abc123",
        {"kind": "answer", "title": "Why?", "question": "Why?", "files": [{"path": "app/secret_name.py"}]},
        "Because.",
    )
    write(guide, "areas/app", {"kind": "area", "title": ["not", "a", "string"]}, "The app.")
    documents = {document.id: document for document in guide_documents(guide, None)}
    retry = documents["concepts/retry"]
    assert retry.kind == "concept" and retry.title == "Payment retries"
    assert retry.headings == "How it works"
    assert "retried" in retry.text and "module:app/retry.py" in retry.text and "We retry." in retry.text
    assert "[!documented]" not in retry.text and "diagram" not in retry.text and "**" not in retry.text
    assert "zanzibar" not in " ".join(vars(document).__repr__() for document in documents.values())
    assert "secret_name" not in repr(documents["answers/2026-10-05-why-abc123"])
    assert documents["areas/app"].link == "/pages/areas/app"


def test_guide_documents_include_facts_and_decisions(tmp_path: Path) -> None:
    connection = connect(tmp_path / "codetrail.db")
    store = FactStore(connection)
    store.record(
        "c1",
        [
            Entity("module:app/retry.py", EntityKind.MODULE, {"name": "app.retry"}, (Source("app/retry.py"),)),
            Entity(
                "decision:0007",
                EntityKind.DECISION,
                {"number": "0007", "title": "Retry twice"},
                (Source("docs/adr/0007.md"),),
            ),
        ],
        [],
    )
    documents = {document.id: document for document in guide_documents(GuideRepository(tmp_path / "guide"), store)}
    fact = documents["facts/module:app/retry.py"]
    assert (fact.kind, fact.title, fact.link) == ("fact", "module:app/retry.py", "/facts/module:app/retry.py")
    assert "module" in fact.text and "app.retry" in fact.text
    decision = documents["decisions/decision:0007"]
    assert (decision.kind, decision.title, decision.link) == ("decision", "0007. Retry twice", "/facts/decision:0007")
    connection.close()
