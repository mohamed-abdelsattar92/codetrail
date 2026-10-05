"""Search over the guide: an in-memory SQLite FTS5 index of pages, saved answers, decisions and facts (design 16.3).

Only what the reader can already see in the guide is indexed: each page's title and body (never its front matter,
which holds the checks' rubrics), and the names and kinds of facts. Source files are never read. Queries never reach
FTS5's query language: every word is quoted as a literal, the last one matches as a prefix, and the expression is
always a bound parameter. Snippets are plain text, split into segments that say whether they matched.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from markdown_it import MarkdownIt

from codetrail.facts import EntityKind
from codetrail.facts.store import FactStore
from codetrail.generate.validate import DIAGRAM, FACT_LINK, MARKER
from codetrail.guide import GuideRepository

WORD = re.compile(r"\w+")
SNIPPET_CHARS = 160
PARSER = MarkdownIt("commonmark").enable("table")
# Title, headings and text, weighted for bm25; the other columns are stored, not searched.
SCHEMA = (
    "CREATE VIRTUAL TABLE documents USING fts5(title, headings, text, id UNINDEXED, kind UNINDEXED, "
    "link UNINDEXED, tokenize = 'unicode61 remove_diacritics 2')"
)


@dataclass(frozen=True)
class Document:
    id: str  # a page id, "facts/<fact id>" or "decisions/<fact id>"
    kind: str  # "area", "concept", "path", "digest", "answer", "decision" or "fact"
    title: str
    headings: str
    text: str
    link: str


@dataclass(frozen=True)
class Result:
    document: Document
    snippet: list[tuple[str, bool]]  # plain-text segments, and whether each one matched


def match_expression(query: str, max_chars: int) -> str | None:
    """The query as an FTS5 expression of quoted words, the last a prefix; None when no word is left."""
    words = [word for word in query[:max_chars].split() if WORD.search(word)]
    if not words:
        return None
    quoted = ['"' + word.replace('"', '""') + '"' for word in words]
    return " ".join(quoted) + "*"


def _fold(text: str) -> str:
    """Lower case without accents, the way the index's tokenizer compares words."""
    return "".join(char for char in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(char))


def _snippet(text: str, query: str, max_chars: int) -> list[tuple[str, bool]]:
    terms = [_fold(word) for word in WORD.findall(query[:max_chars])]
    if not text or not terms:
        return [(text[:SNIPPET_CHARS], False)] if text else []

    def matches(word: str) -> bool:
        folded = _fold(word)
        return folded in terms[:-1] or folded.startswith(terms[-1])

    hits = [match for match in WORD.finditer(text) if matches(match.group())]
    start = max(0, hits[0].start() - SNIPPET_CHARS // 3) if hits else 0
    if start > 0:  # begin at a word, not inside one
        space = text.find(" ", start)
        start = space + 1 if 0 <= space < (hits[0].start() if hits else len(text)) else start
    end = min(len(text), start + SNIPPET_CHARS)
    if end < len(text):
        space = text.rfind(" ", start, end)
        end = space if space > (hits[0].end() if hits else start) else end
    segments: list[tuple[str, bool]] = [("…", False)] if start > 0 else []
    position = start
    for hit in hits:
        if hit.start() < start or hit.end() > end:
            continue
        if hit.start() > position:
            segments.append((text[position : hit.start()], False))
        segments.append((hit.group(), True))
        position = hit.end()
    if position < end:
        segments.append((text[position:end], False))
    if end < len(text):
        segments.append(("…", False))
    return segments


class SearchIndex:
    """One in-memory index, shared by the page's requests; every use holds the lock."""

    def __init__(self) -> None:
        self._connection = sqlite3.connect(":memory:", check_same_thread=False)
        self._connection.execute(SCHEMA)
        self._lock = threading.Lock()

    def rebuild(self, documents: Iterable[Document]) -> None:
        rows = [_row(document) for document in documents]
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM documents")
            self._connection.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)", rows)

    def add(self, document: Document) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM documents WHERE id = ?", (document.id,))
            self._connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)", _row(document))

    def search(self, query: str, max_chars: int, limit: int) -> list[Result]:
        expression = match_expression(query, max_chars)
        if expression is None:
            return []
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT title, headings, text, id, kind, link FROM documents WHERE documents MATCH ?"
                    " ORDER BY bm25(documents, 10.0, 4.0, 1.0) LIMIT ?",
                    (expression, limit),
                ).fetchall()
            except sqlite3.OperationalError:  # every word is quoted, so this is FTS5 refusing an odd token
                return []
        results = []
        for title, headings, text, identifier, kind, link in rows:
            document = Document(identifier, kind, title, headings, text, link)
            results.append(Result(document, _snippet(text, query, max_chars)))
        return results


def _row(document: Document) -> tuple[str, str, str, str, str, str]:
    return (document.title, document.headings, document.text, document.id, document.kind, document.link)


def _plain(body: str) -> tuple[str, str]:
    """A page body's headings and its text, without Markdown, rationale markers or diagram placeholders."""
    lines = [line for line in body.splitlines() if not MARKER.match(line) and not DIAGRAM.match(line.strip())]
    source = FACT_LINK.sub(lambda match: match.group(1), "\n".join(lines))
    headings: list[str] = []
    pieces: list[str] = []
    in_heading = False
    for token in PARSER.parse(source):
        if token.type == "heading_open":
            in_heading = True
        elif token.type == "heading_close":
            in_heading = False
        elif token.type == "inline":
            words = "".join(child.content for child in token.children or [] if child.type in ("text", "code_inline"))
            (headings if in_heading else pieces).append(words)
        elif token.type in ("fence", "code_block"):
            pieces.append(token.content.strip())
    return "\n".join(headings), " ".join(piece for piece in pieces if piece)


def guide_documents(guide: GuideRepository, store: FactStore | None) -> list[Document]:
    """Everything search covers: the guide's pages (title and body only), decisions, and facts by name and kind."""
    documents = []
    for page in guide.pages() if guide.root.exists() else []:
        headings, text = _plain(page.body)
        documents.append(Document(page.id, page.kind, page.title, headings, text, f"/pages/{page.id}"))
    for entity in store.entities() if store is not None else []:
        if entity.kind == EntityKind.DECISION:
            title = f"{entity.attributes.get('number', '')}. {entity.attributes.get('title', '')}".strip(". ")
            text = str(entity.attributes.get("status", ""))
            documents.append(Document(f"decisions/{entity.id}", "decision", title, "", text, f"/facts/{entity.id}"))
        else:
            text = f"{entity.kind} {entity.attributes.get('name', '')}".strip()
            documents.append(Document(f"facts/{entity.id}", "fact", entity.id, "", text, f"/facts/{entity.id}"))
    return documents
