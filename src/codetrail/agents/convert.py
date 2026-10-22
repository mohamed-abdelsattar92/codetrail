"""Guide pages turned into Markdown for coding agents (design section 23.2).

Rationale blocks become one marked line each, so an agent can tell a decision from a reading of the code; fact links
become their ids; diagram placeholders name the diagram; and every `@` becomes fullwidth, so an agent that attaches
the file an `@path` names attaches nothing.
"""

from __future__ import annotations

import re

from codetrail.assistant.prompts import neutralize
from codetrail.generate.validate import DIAGRAM, FACT_LINK, MARKER, QUOTE_MARKS, normalize, parse_rationale
from codetrail.guide import Page

SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def to_agent_markdown(body: str) -> str:
    lines = body.splitlines()
    converted: list[str] = []
    index = 0
    while index < len(lines):
        marker = MARKER.match(lines[index])
        if marker:
            index += 1
            content = []
            while index < len(lines) and lines[index].startswith(">") and not MARKER.match(lines[index]):
                content.append(lines[index][1:])
                index += 1
            text = _plain(normalize(" ".join(content)))
            if marker.group(1) == "documented":
                converted.append(f'[documented: {marker.group(2)}] "{text.strip(QUOTE_MARKS + " ")}"')
            else:
                converted.append(f"[inferred] {text}")
            continue
        diagram = DIAGRAM.match(lines[index].strip())
        if diagram:
            converted.append(f"(diagram: {normalize(diagram.group(1))}; see the page in Codetrail)")
        else:
            converted.append(_plain(lines[index]))
        index += 1
    return neutralize("\n".join(converted).rstrip() + "\n")


def derived_brief(page: Page, max_words: int) -> str:
    """A brief for a page written before pages had one, in page syntax: its title, its first paragraph, the files
    it was written from and the first sentence of each rationale block, cut to `max_words`."""
    parts = [f"# {page.title}"]
    paragraph = _first_paragraph(page.body)
    if paragraph:
        parts.append(paragraph)
    files = [str(item.get("path")) for item in page.meta.get("files") or [] if item.get("path")]
    if files:
        parts.append("Key files: " + ", ".join(files))
    for block in parse_rationale(page.body):
        sentence = SENTENCE_END.split(normalize(block.text).strip(QUOTE_MARKS + " "), maxsplit=1)[0]
        if block.kind == "documented":
            parts.append(f'> [!documented] {block.citation}\n> "{sentence}"')
        else:
            parts.append(f"> [!inferred]\n> {sentence}")
    return limit_words("\n\n".join(parts), max_words)[0]


def limit_words(text: str, max_words: int) -> tuple[str, int]:
    """The text cut after `max_words` words, keeping whole lines where it can, and how many words it left out."""
    total = len(text.split())
    if total <= max_words:
        return text, 0
    kept: list[str] = []
    count = 0
    for line in text.splitlines():
        words = line.split()
        if count + len(words) > max_words:
            if max_words > count:
                kept.append(" ".join(words[: max_words - count]))
            break
        kept.append(line)
        count += len(words)
    return "\n".join(kept).rstrip(), total - max_words


def _plain(text: str) -> str:
    return FACT_LINK.sub(lambda link: f"`{link.group(1)}`", text)


def _first_paragraph(body: str) -> str:
    for paragraph in body.split("\n\n"):
        stripped = paragraph.strip()
        if stripped and not stripped.startswith(("#", ">", "{{", "-", "|")):
            return normalize(stripped)
    return ""
