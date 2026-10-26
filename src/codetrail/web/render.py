"""Rendering a guide page (design sections 6.4 and 7.4).

Markdown is rendered by markdown-it-py with raw HTML disabled and its link validation on (no `javascript:`,
`vbscript:`, `file:` or `data:` links). Rationale blocks become labelled boxes, `[[fact]]` links point at the fact's
view, and diagram placeholders are replaced by diagrams drawn from facts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
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


def _aligned_cell(
    renderer: RendererHTML, tokens: Sequence[Token], index: int, options: OptionsDict, env: EnvType
) -> str:
    """A table column's alignment as a class, where markdown-it writes an inline style: the page's policy allows no
    inline styles (ADR 0016)."""
    token = tokens[index]
    style = token.attrs.pop("style", None)
    if isinstance(style, str) and style.startswith("text-align:"):
        token.attrSet("class", "align-" + style.removeprefix("text-align:"))
    return renderer.renderToken(tokens, index, options, env)


for cell in ("th_open", "td_open"):
    MARKDOWN.add_render_rule(cell, _aligned_cell)


@dataclass(frozen=True)
class Segment:
    kind: str  # "html", "rationale" or "diagram"
    html: Markup = field(default_factory=Markup)
    label: str = ""
    citation: str | None = None
    link: str | None = None
    diagram: Diagram | None = None


def render_markdown(text: str) -> Markup:
    linked = FACT_LINK.sub(lambda match: f"[{match.group(1)}](</facts/{quote(match.group(1), safe='')}>)", text)
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
    return f"/source/{quote(match.group('path'))}#L{match.group('start')}" if match else None


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
