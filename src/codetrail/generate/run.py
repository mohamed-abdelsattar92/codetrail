"""Writing the guide during an update (design section 6).

The outline is planned on the first run and extended for new facts no page covers; affected pages are ranked and
written within the budget, `concurrency` at a time; each draft is validated, retried once with its problems, and kept
out of the guide if it fails again; a digest records what changed. Everything is committed once at the end, and any
unexpected failure discards the uncommitted changes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import anyio

from codetrail.claude import Claude, ClaudeError, DigestRequest, PageRequest, PlanRequest
from codetrail.errors import CodetrailError
from codetrail.facts import EntityKind, FactDiff
from codetrail.facts.store import FactStore
from codetrail.generate.outline import (
    OutlineEntry,
    outline_data,
    outline_entries,
    uncovered_facts,
    validate_outline,
)
from codetrail.generate.scope import affected_reason, changed_fact_count, facts_in_scope, page_meta
from codetrail.generate.validate import ValidationContext, validate_page
from codetrail.guide import GuideRepository, Page
from codetrail.repo.history import Commit, commits_between, recent_commits
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest

MAX_FACT_LINES = 200
HISTORY_COMMITS = 25


@dataclass
class GenerationContext:
    target: str
    guide: GuideRepository
    store: FactStore
    manifest: SourceManifest
    source_root: Path
    mirror: Path
    scanner: SecretScanner
    visible: Callable[[str], bool]
    max_pages: int
    concurrency: int
    previous_commit: str | None  # the snapshot before this update, or None on the first
    diff: FactDiff


@dataclass
class GenerationResult:
    written: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    left_for_later: list[str] = field(default_factory=list)
    outline_problems: list[str] = field(default_factory=list)
    digest: str | None = None
    commit: str | None = None
    cost_usd: float = 0.0


async def generate_guide(context: GenerationContext, claude: Claude) -> GenerationResult:
    guide = context.guide
    guide.ensure()
    if guide.has_uncommitted_changes():
        raise CodetrailError(
            f"The guide at {guide.root} has uncommitted edits; commit or discard them before updating."
        )
    result = GenerationResult()
    try:
        entries = await _outline(context, claude, result)
        affected = []
        for entry in entries:
            page = guide.read_page(entry.id)
            if affected_reason(entry, page, context.store) is not None:
                affected.append((changed_fact_count(entry, page, context.store), entry))
        affected.sort(key=lambda item: -item[0])
        to_write = [entry for _, entry in affected[: context.max_pages]]
        result.left_for_later = [entry.id for _, entry in affected[context.max_pages :]]
        validation = ValidationContext(context.source_root, context.manifest, context.store, context.mirror,
                                       context.scanner)  # fmt: skip
        limiter = anyio.Semaphore(context.concurrency)

        async def write(entry: OutlineEntry) -> None:
            async with limiter:
                await _write_page(entry, context, claude, validation, result)

        try:
            async with anyio.create_task_group() as group:
                for entry in to_write:
                    group.start_soon(write, entry)
        except ExceptionGroup as errors:  # one page's unexpected error is the update's error
            if len(errors.exceptions) == 1:
                raise errors.exceptions[0] from None
            raise
        result.written.sort()
        await _write_digest(context, claude, validation, result)
        result.commit = guide.commit(
            f"Update to {context.manifest.commit[:12]}: {len(result.written)} pages"
            + (", a digest" if result.digest else "")
        )
    except BaseException:
        guide.discard()
        raise
    return result


async def _outline(context: GenerationContext, claude: Claude, result: GenerationResult) -> list[OutlineEntry]:
    guide, store = context.guide, context.store
    entries = outline_entries(guide.read_outline())
    if not entries:
        draft = await claude.plan(PlanRequest(context.target, facts_summary(store, context.manifest), ""))
        result.cost_usd += draft.cost_usd
        entries, problems = validate_outline(draft.pages, store, context.manifest)
        result.outline_problems += problems
        if not entries:
            raise CodetrailError("Claude's outline had no page Codetrail could use: " + "; ".join(problems[:3]))
        guide.write_outline(outline_data(entries))
        return entries
    new_facts = set(context.diff.added_entities)
    uncovered = [fact for fact in uncovered_facts(store, entries) if fact in new_facts]
    if uncovered:
        current = json.dumps(outline_data(entries), indent=1)
        draft = await claude.plan(PlanRequest(context.target, facts_summary(store, context.manifest), current,
                                              uncovered[:200]))  # fmt: skip
        result.cost_usd += draft.cost_usd
        added, problems = validate_outline(draft.pages, store, context.manifest, existing=entries)
        result.outline_problems += problems
        if added:
            entries = [*entries, *added]
            guide.write_outline(outline_data(entries))
    return entries


async def _write_page(
    entry: OutlineEntry,
    context: GenerationContext,
    claude: Claude,
    validation: ValidationContext,
    result: GenerationResult,
) -> None:
    request = PageRequest(
        entry.id, entry.kind, entry.title, entry.scope_paths,
        facts_text(context.store, entry), decisions_text(context.store), history_text(context, entry.scope_paths),
    )  # fmt: skip
    problems: list[str] = []
    try:
        for _attempt in range(2):
            draft = await claude.write_page(request)
            result.cost_usd += draft.cost_usd
            problems = validate_page(draft.body, draft.checks, validation)
            if not problems:
                meta = page_meta(entry, context.store, context.manifest, draft.files_read)
                meta["checks"] = draft.checks
                context.guide.write_page(Page(entry.id, meta, draft.body))
                result.written.append(entry.id)
                return
            request = replace(request, problems=problems, previous_body=draft.body)
    except ClaudeError as error:
        result.failed.append((entry.id, str(error)))
        return
    result.failed.append((entry.id, "; ".join(problems[:3])))


async def _write_digest(
    context: GenerationContext, claude: Claude, validation: ValidationContext, result: GenerationResult
) -> None:
    head = context.manifest.commit
    digests = sorted(context.guide.pages("digest"), key=lambda page: str(page.meta.get("written_at", "")))
    since = str(digests[-1].meta.get("to_commit")) if digests else context.previous_commit
    if not digests and result.written:
        since = None  # the guide's first pages: record its creation, whatever facts-only updates came before
    if since == head or (since is None and not result.written):
        return
    commits = commits_between(context.mirror, since, head, context.visible, context.scanner) if since else []
    if since and not commits and not result.written:
        return
    page_id = f"digests/{datetime.now(UTC).strftime('%Y-%m-%d')}-{head[:12]}"
    meta = {"id": page_id, "kind": "digest", "generated": True, "from_commit": since, "to_commit": head,
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"), "pages_changed": result.written}  # fmt: skip
    if not commits:
        meta["title"] = "The guide was created"
        body = "The guide's first pages:\n\n" + "\n".join(f"- [{page}](/pages/{page})" for page in result.written)
        context.guide.write_page(Page(page_id, meta, body))
        result.digest = page_id
        return
    request = DigestRequest(context.target, commits_text(commits), fact_changes_text(context.diff), result.written)
    try:
        draft = await claude.write_digest(request)
        result.cost_usd += draft.cost_usd
        if validate_page(draft.body, None, validation):
            raise ClaudeError("the digest failed validation")
        meta["title"], body = draft.title, draft.body
    except ClaudeError:
        meta["title"] = f"{len(commits)} commits since the last update"
        body = "Claude's digest couldn't be checked, so here are the commits:\n\n" + "\n".join(
            f"- {commit.subject}" for commit in commits
        )
    context.guide.write_page(Page(page_id, meta, body))
    result.digest = page_id


def facts_summary(store: FactStore, manifest: SourceManifest) -> str:
    """A rolled-up view of the facts for planning: folders, projects, decisions, packages."""
    lines = ["Top-level folders (visible files):"]
    folders: dict[str, int] = {}
    for path in manifest.files:
        parts = path.split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else parts[0] if len(parts) > 1 else "(root)"
        folders[key] = folders.get(key, 0) + 1
    lines += [f"- {folder}: {count}" for folder, count in sorted(folders.items())]
    for kind in (EntityKind.PROJECT, EntityKind.DECISION, EntityKind.PACKAGE):
        lines.append(f"\n{kind} facts:")
        lines += [f"- {entity.id} {json.dumps(dict(entity.attributes))}" for entity in store.entities(kind)]
    modules: dict[str, int] = {}
    for entity in store.entities(EntityKind.MODULE):
        folder = "/".join(entity.id.removeprefix("module:").split("/")[:-1])
        modules[folder] = modules.get(folder, 0) + 1
    lines.append("\nmodule facts, counted by folder (ids are module:<path>):")
    lines += [f"- {folder}: {count}" for folder, count in sorted(modules.items())]
    return "\n".join(lines)


def facts_text(store: FactStore, entry: OutlineEntry) -> str:
    entities = facts_in_scope(store, entry)
    lines = [f"{entity.kind} {entity.id} {json.dumps(dict(entity.attributes))}" for entity in entities]
    if len(lines) > MAX_FACT_LINES:
        lines = [*lines[:MAX_FACT_LINES], f"... and {len(lines) - MAX_FACT_LINES} more in the same scope"]
    return "\n".join(lines) or "(none)"


def decisions_text(store: FactStore) -> str:
    return "\n".join(
        f"- {entity.id} ({entity.attributes.get('status', '?')}): {entity.attributes.get('title', '')}"
        f" — {entity.attributes.get('path', '')}"
        for entity in store.entities(EntityKind.DECISION)
    )


def history_text(context: GenerationContext, scope_paths: Sequence[str]) -> str:
    commits = recent_commits(context.mirror, context.manifest.commit, scope_paths, HISTORY_COMMITS, context.visible,
                             context.scanner)  # fmt: skip
    return commits_text(commits)


def commits_text(commits: Sequence[Commit]) -> str:
    blocks = []
    for commit in commits:
        why = _section(commit.body, "Why")
        blocks.append(f"- {commit.sha[:12]} {commit.date[:10]} {commit.subject}" + (f"\n  Why: {why}" if why else ""))
    return "\n".join(blocks)


def fact_changes_text(diff: FactDiff) -> str:
    lines = [f"added {fact}" for fact in diff.added_entities[:60]]
    lines += [f"changed {fact}" for fact in diff.changed_entities[:60]]
    lines += [f"removed {fact}" for fact in diff.removed_entities[:60]]
    return "\n".join(lines)


def _section(body: str, name: str) -> str:
    """The text of a commit body's section, written as a heading line or inline ("Why: ...")."""
    lines = body.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == name or stripped.startswith(f"{name}:"):
            collected = [stripped[len(name) + 1 :].strip()] if stripped != name else []
            for following in lines[index + 1 :]:
                if not following.strip() or (following.strip().endswith(":") and following[:1].isupper()):
                    break
                collected.append(following.strip())
            return " ".join(part for part in collected if part)[:600]
    return ""
