"""Rendering a guide page (design sections 6.4 and 7.4).

Markdown is rendered by markdown-it-py with raw HTML disabled and its link validation on (no `javascript:`,
`vbscript:`, `file:` or `data:` links). Rationale blocks become labelled boxes, `[[fact]]` links point at the fact's
view, and diagram placeholders are replaced by diagrams drawn from facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from markdown_it import MarkdownIt
from markupsafe import Markup

from codetrail.facts.store import FactStore
from codetrail.generate.validate import DIAGRAM, FILE_CITATION, MARKER
from codetrail.web.diagrams import (
    Diagram,
    dependencies_diagram,
    imports_diagram,
    resources_diagram,
    system_diagram,
)

FACT_LINK = re.compile(r"\[\[([a-z_]+:[^\]\s]+)\]\]")
MARKDOWN = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


@dataclass(frozen=True)
class Segment:
    kind: str  # "html", "rationale" or "diagram"
    html: Markup = field(default_factory=Markup)
    label: str = ""
    citation: str | None = None
    link: str | None = None
    diagram: Diagram | None = None


def render_markdown(text: str) -> Markup:
    linked = FACT_LINK.sub(lambda match: f"[{match.group(1)}](</facts/{match.group(1)}>)", text)
    # Raw HTML is off and links are validated, so the output holds only markdown-it's own markup.
    return Markup(MARKDOWN.render(linked))  # noqa: S704


def render_body(body: str, store: FactStore, max_nodes: int) -> list[Segment]:
    segments: list[Segment] = []
    pending: list[str] = []

    def flush() -> None:
        if "".join(pending).strip():
            segments.append(Segment("html", render_markdown("\n".join(pending))))
        pending.clear()

    lines = body.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = MARKER.match(line)
        diagram = DIAGRAM.match(line.strip())
        if marker:
            flush()
            index += 1
            content = []
            while index < len(lines) and lines[index].startswith(">") and not MARKER.match(lines[index]):
                content.append(lines[index][1:].removeprefix(" "))
                index += 1
            citation = marker.group(2) or None
            segments.append(
                Segment("rationale", render_markdown("\n".join(content)), marker.group(1), citation, _link(citation))
            )
            continue
        if diagram:
            flush()
            drawn = _diagram(diagram.group(1), store, max_nodes)
            if drawn is not None and drawn.nodes:  # a diagram with nothing to draw is left out, not shown empty
                segments.append(Segment("diagram", diagram=drawn))
        else:
            pending.append(line)
        index += 1
    flush()
    return segments


def _link(citation: str | None) -> str | None:
    match = FILE_CITATION.match(citation or "")
    return f"/source/{match.group('path')}#L{match.group('start')}" if match else None


def _diagram(arguments: str, store: FactStore, max_nodes: int) -> Diagram | None:
    parts = arguments.split()
    if parts == ["system"]:
        return system_diagram(store, None, max_nodes)
    if len(parts) != 2 or "=" not in parts[1]:
        return None
    kind, value = parts[0], parts[1].split("=", 1)[1]
    if kind == "imports":
        return imports_diagram(store, value, max_nodes)
    if kind == "dependencies":
        return dependencies_diagram(store, value)
    if kind == "resources":
        return resources_diagram(store, value, max_nodes)
    if kind == "system" and parts[1].startswith("focus="):
        return system_diagram(store, value, max_nodes)
    return None
