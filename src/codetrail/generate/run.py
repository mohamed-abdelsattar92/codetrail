"""Writing the guide during an update (design section 6).

The outline is planned on the first run and extended for new facts no page covers; affected pages are ranked and
written within the budget, `concurrency` at a time; each draft is validated, retried once with its problems, and kept
out of the guide if it fails again; a digest records what changed. Everything is committed once at the end, and any
unexpected failure discards the uncommitted changes. Each step is reported to `progress` as it happens, for the
page's update panel.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio

from codetrail.assistant import (
    Assistant,
    AssistantError,
    DigestDraft,
    DigestRequest,
    PageDraft,
    PageRequest,
    PaidCallFailed,
    PlanDraft,
    PlanRequest,
    Usage,
)
from codetrail.assistant.routing import CallBound
from codetrail.assistant.usage import UsageLog
from codetrail.errors import CodetrailError
from codetrail.facts import EntityKind, FactDiff
from codetrail.facts.store import FactStore
from codetrail.generate.failures import FailedPages
from codetrail.generate.outline import (
    OutlineEntry,
    OutlinePath,
    outline_data,
    outline_entries,
    outline_paths,
    uncovered_facts,
    validate_outline,
    validate_paths,
)
from codetrail.generate.revise import apply_sections
from codetrail.generate.scope import (
    affected_reason,
    changed_fact_count,
    facts_in_scope,
    page_meta,
    page_snapshot,
    scope_changes,
    scope_hash,
)
from codetrail.generate.validate import (
    FLAGGED_PROBLEM,
    ValidationContext,
    validate_brief,
    validate_highlights,
    validate_page,
)
from codetrail.guide import GuideRepository, Page
from codetrail.repo.history import Commit, commits_between, recent_commits
from codetrail.repo.secrets import SecretScanner
from codetrail.repo.source import SourceManifest

MAX_FACT_LINES = 200
PAGE_ATTEMPTS = 2  # a page that fails validation is written once more
HISTORY_COMMITS = 25

Progress = Callable[[dict[str, object]], None]  # one step of an update, such as {"step": "page", "title": ...}


def no_progress(event: dict[str, object]) -> None:
    """Reports nowhere: the command line prints its summary at the end instead."""


TEAM_BUDGET_SPENT = "The team's budget is spent, so this repository wasn't updated."
BUDGET_BELOW_CAP = (
    "The update's budget (${budget:.2f}) is smaller than one call's cap (${cap:.2f}), so no call can start; raise "
    "generation.max_budget_usd_per_update, or lower generation.max_budget_usd_per_call (for a priced Codex model, "
    "providers.codex.max_tokens_per_call)."
)
ROUNDING_USD = 1e-9  # so caps that add up to the limit exactly still fit, whatever floating point makes of the sum


class SharedBudget:
    """A spending limit that calls running together count against: an update's pages, or every update of a team's
    catch-up, in threads (design 6.7, 24.8). Each call reserves its cap before it starts and settles with what it
    cost when it ends, so the calls in flight can never take the total past the limit."""

    def __init__(self, limit_usd: float) -> None:
        self._limit_usd = limit_usd
        self._spent_usd = 0.0
        self._reserved_usd = 0.0
        self._lock = threading.Lock()

    def reserve(self, cap_usd: float) -> bool:
        """Reserves a call's cap if it fits beside what is spent and reserved; False, reserving nothing, if not."""
        with self._lock:
            if self._spent_usd + self._reserved_usd + cap_usd > self._limit_usd + ROUNDING_USD:
                return False
            self._reserved_usd += cap_usd
            return True

    def settle(self, cap_usd: float, cost_usd: float) -> None:
        """Ends a reserved call: its cap is released and what it cost is spent."""
        with self._lock:
            self._reserved_usd -= cap_usd
            self._spent_usd += cost_usd

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent_usd

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self._limit_usd


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
    max_budget_usd: float
    previous_commit: str | None  # the snapshot before this update, or None on the first
    diff: FactDiff
    # Each kind of call's bound (plan, write, digest), reserved in the update's budget and the team's before it starts,
    # so a call can't start past either (6.7).
    call_bounds: Mapping[str, CallBound]
    learned: set[str] = field(default_factory=set)  # pages the reader has learned: rewritten first
    usage: UsageLog | None = None  # records each call's usage; its costs count against the update's budget
    max_tokens: int | None = None  # the update's token budget, which counts every provider, priced or not
    progress: Progress = no_progress
    failed_pages: FailedPages | None = None  # pages that failed validation, skipped until their scope changes
    retry_failed: bool = False  # try them anyway
    revise_max_changes: int = 0  # an affected page with at most this many changes in its scope is revised (6.3)
    shared_budget: SharedBudget | None = None  # a team catch-up's budget, which every update in it counts against
    max_highlights: int = 0  # the highlights a digest carries for the team page (24)
    max_brief_words: int = 300  # the brief for coding agents each page carries (23.3)
    budget: SharedBudget = field(init=False)  # the update's own, from max_budget_usd

    def __post_init__(self) -> None:
        self.budget = SharedBudget(self.max_budget_usd)


@dataclass
class GenerationResult:
    written: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    left_for_later: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # failed before, and nothing in their scope changed since
    outline_problems: list[str] = field(default_factory=list)
    digest: str | None = None
    commit: str | None = None
    cost_usd: float = 0.0
    tokens: int = 0


async def generate_guide(context: GenerationContext, claude: Assistant) -> GenerationResult:
    guide = context.guide
    guide.ensure()
    if guide.has_uncommitted_changes():
        raise CodetrailError(
            f"The guide at {guide.root} has uncommitted edits; commit or discard them before updating."
        )
    _check_budget(context)
    result = GenerationResult()
    try:
        entries = await _outline(context, claude, result)
        selection = select_pages(context, entries)
        to_write = selection.to_write
        result.left_for_later = [entry.id for entry in selection.left_for_later]
        result.skipped = [entry.id for entry in selection.skipped]
        for entry in selection.skipped:
            context.progress({"step": "page_skipped", "id": entry.id, "title": entry.title})
        validation = ValidationContext(context.source_root, context.manifest, context.store, context.mirror,
                                       context.scanner)  # fmt: skip
        limiter = anyio.Semaphore(context.concurrency)

        async def write(planned: PlannedPage) -> None:
            async with limiter:
                await _write_page(planned, context, claude, validation, result)

        try:
            async with anyio.create_task_group() as group:
                for planned in to_write:
                    group.start_soon(write, planned)
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
        context.progress({"step": "committed", "pages": len(result.written), "failed": len(result.failed),
                          "later": len(result.left_for_later)})  # fmt: skip
    except BaseException:
        guide.discard()
        raise
    return result


async def _outline(context: GenerationContext, claude: Assistant, result: GenerationResult) -> list[OutlineEntry]:
    guide, store = context.guide, context.store
    stored = guide.read_outline()
    entries = outline_entries(stored)
    paths: list[OutlinePath] = []
    if not entries:
        if context.shared_budget is not None and context.shared_budget.exhausted:
            raise CodetrailError(TEAM_BUDGET_SPENT)
        if not _reserve(context, result, "plan"):  # the update's own budget holds it: _check_budget made sure
            raise CodetrailError(TEAM_BUDGET_SPENT)
        request = PlanRequest(context.target, facts_summary(store, context.manifest), "")
        draft, spent = await _plan(context, claude, result, request)
        entries, problems = validate_outline(draft.pages, store, context.manifest, context.scanner)
        context.progress({"step": "planned", "pages": len(entries), **spent})
        result.outline_problems += problems
        if not entries:
            raise CodetrailError("The assistant's outline had no page Codetrail could use: " + "; ".join(problems[:3]))
        paths, problems = validate_paths(draft.paths, {entry.id for entry in entries}, context.scanner)
        result.outline_problems += problems
    else:
        paths = outline_paths(stored, {entry.id for entry in entries})
        new_facts = set(context.diff.added_entities)
        uncovered = [fact for fact in uncovered_facts(store, entries) if fact in new_facts]
        if uncovered and _reserve(context, result, "plan"):
            current = json.dumps(outline_data(entries, paths), indent=1)
            request = PlanRequest(context.target, facts_summary(store, context.manifest), current, uncovered[:200])
            draft, spent = await _plan(context, claude, result, request)
            added, problems = validate_outline(draft.pages, store, context.manifest, context.scanner, entries)
            context.progress({"step": "planned", "pages": len(added), **spent})
            result.outline_problems += problems
            entries = [*entries, *added]
        # an outline written before paths existed: plan them once
        if stored is not None and "paths" not in stored and _reserve(context, result, "plan"):
            current = json.dumps(outline_data(entries), indent=1)
            try:
                request = PlanRequest(context.target, "", current, paths_only=True)
                draft, spent = await _plan(context, claude, result, request)
            except AssistantError as error:
                result.outline_problems.append(f"Guided paths couldn't be planned: {error}")
            else:
                context.progress({"step": "planned", "pages": 0, **spent})
                paths, problems = validate_paths(draft.paths, {entry.id for entry in entries}, context.scanner)
                result.outline_problems += problems
    if outline_data(entries, paths) != stored:
        guide.write_outline(outline_data(entries, paths))
    _write_paths(context, entries, paths)
    return entries


def _check_budget(context: GenerationContext) -> None:
    """Refuses, before anything is called, an update that would make a paid call its own budget can't hold even once
    (design section 6.7)."""
    work = planned_work(context)
    kinds = [kind for kind, due in (("plan", work.plan_calls), ("write", work.page_calls_max),
                                    ("digest", work.digest_possible)) if due]  # fmt: skip
    cap = max((context.call_bounds[kind].cap_usd for kind in kinds), default=0.0)
    if cap > context.max_budget_usd + ROUNDING_USD:
        raise CodetrailError(BUDGET_BELOW_CAP.format(budget=context.max_budget_usd, cap=cap))


def _bound(context: GenerationContext, kind: str) -> CallBound:
    return context.call_bounds["write" if kind == "revise" else kind]  # a revision is a write call


def _reserve(context: GenerationContext, result: GenerationResult, kind: str) -> bool:
    """Reserves one call's bound in the update's budget and in the team's, before the call starts; False, reserving
    nothing, when it doesn't fit in either or the update's tokens are spent. Every reserved call is settled, by
    `_paid`, with what it cost."""
    if context.max_tokens is not None and result.tokens >= context.max_tokens:
        return False
    cap = _bound(context, kind).cap_usd
    if not context.budget.reserve(cap):
        return False
    if context.shared_budget is not None and not context.shared_budget.reserve(cap):
        context.budget.settle(cap, 0.0)
        return False
    return True


def _settle(context: GenerationContext, kind: str, cost_usd: float) -> None:
    cap = _bound(context, kind).cap_usd
    context.budget.settle(cap, cost_usd)
    if context.shared_budget is not None:
        context.shared_budget.settle(cap, cost_usd)


async def _paid[Draft: (PlanDraft, PageDraft, DigestDraft)](
    context: GenerationContext, result: GenerationResult, kind: str, call: Callable[[], Awaitable[Draft]]
) -> tuple[Draft, dict[str, object]]:
    """Makes a call whose bound `_reserve` reserved, and settles it: with its cost; with what a call that failed after
    the provider counted it used; with nothing when it failed before using anything (it couldn't start, say); and
    with its whole bound when it was cancelled while its program ran, since no result says what it used."""
    try:
        draft = await call()
    except PaidCallFailed as error:
        _spend(context, result, kind, error.usage, error.cost_usd)
        raise
    except AssistantError:
        _settle(context, kind, 0.0)
        raise
    except BaseException:
        bound = _bound(context, kind)
        if bound.cap_usd:
            _spend(context, result, kind, Usage(bound.provider, bound.model, cost_usd=bound.cap_usd), bound.cap_usd)
        else:
            _settle(context, kind, 0.0)
        raise
    return draft, _spend(context, result, kind, draft.usage, draft.cost_usd)


async def _plan(
    context: GenerationContext, claude: Assistant, result: GenerationResult, request: PlanRequest
) -> tuple[PlanDraft, dict[str, object]]:
    """Asks for a plan, once `_reserve` reserved its cap, and counts what it used."""
    context.progress({"step": "plan"})
    return await _paid(context, result, "plan", partial(claude.plan, request))


def _spend(
    context: GenerationContext, result: GenerationResult, kind: str, usage: Usage, cost_usd: float
) -> dict[str, object]:
    """Records the call's usage, settles its reservation with its cost, and adds its cost and tokens to the update's.
    `cost_usd` is the draft's own figure, used when no usage log prices it.

    Returns what the call used, for the progress report: its provider, model, tokens and counted cost."""
    cost = context.usage.record(kind, usage) if context.usage is not None and usage.provider else cost_usd
    tokens = usage.input_tokens + usage.cached_input_tokens + usage.output_tokens
    result.cost_usd += cost
    _settle(context, kind, cost)
    result.tokens += tokens
    return {"provider": usage.provider, "model": usage.model, "tokens": tokens, "cost_usd": cost}


def _write_paths(context: GenerationContext, entries: Sequence[OutlineEntry], paths: Sequence[OutlinePath]) -> None:
    """Writes each guided path as a page, without Claude: its goal and its steps, by title."""
    titles = {entry.id: entry.title for entry in entries}
    wanted = {path.id for path in paths}
    for page in context.guide.pages("path"):
        if page.id.startswith("paths/") and page.id not in wanted:
            context.guide.path_of(page.id).unlink()
    for path in paths:
        meta = {"id": path.id, "kind": "path", "title": path.title, "generated": True, "goal": path.goal,
                "steps": path.steps}  # fmt: skip
        body = f"{path.goal}\n\n" + "\n".join(
            f"{number}. [{titles.get(step, step)}](/pages/{step})" for number, step in enumerate(path.steps, start=1)
        )
        current = context.guide.read_page(path.id)
        if current is None or current.meta != meta or current.body != body:
            context.guide.write_page(Page(path.id, meta, body))


async def _write_page(
    planned: PlannedPage,
    context: GenerationContext,
    claude: Assistant,
    validation: ValidationContext,
    result: GenerationResult,
) -> None:
    """Writes the page, or revises it when it has `planned.changes`: the assistant returns only the sections to
    replace, which go into the page as it stands; either way the whole page is validated, and retried once."""
    entry = planned.entry
    current = context.guide.read_page(entry.id) if planned.changes else None
    revising = current is not None
    history = history_text(context, entry.scope_paths)
    if current is not None:
        checks = [dict(check) for check in current.meta.get("checks") or []]
        request = PageRequest(entry.id, entry.kind, entry.title, entry.scope_paths, "", "", history,
                              current_body=current.body, current_checks=checks, changes=planned.changes,
                              brief_words=context.max_brief_words,
                              current_brief=str(current.meta.get("brief") or ""))  # fmt: skip
    else:
        request = PageRequest(entry.id, entry.kind, entry.title, entry.scope_paths, facts_text(context.store, entry),
                              decisions_text(context.store), history, brief_words=context.max_brief_words)  # fmt: skip
    problems: list[str] = []
    try:
        for attempt in range(1, PAGE_ATTEMPTS + 1):
            if not _reserve(context, result, "write"):  # the budget can't hold another call: no page, or no retry
                result.left_for_later.append(entry.id)
                context.progress({"step": "page_later", "id": entry.id, "title": entry.title})
                return
            context.progress({"step": "page", "id": entry.id, "title": entry.title, "attempt": attempt,
                              "reason": planned.reason, "changed": planned.changed_now,
                              "revise": revising})  # fmt: skip
            draft, spent = await _paid(context, result, "revise" if revising else "write",
                                       partial(claude.write_page, request))  # fmt: skip
            body, checks, brief = draft.body, draft.checks, draft.brief
            if current is not None:
                body = apply_sections(request.current_body, draft.sections)
                checks = draft.checks or request.current_checks
                brief = draft.brief or request.current_brief  # a revision keeps the brief it doesn't replace
            problems = validate_page(body, checks, validation)
            problems += validate_brief(brief, validation, context.max_brief_words)
            if not problems:
                meta = page_meta(entry, context.store, context.manifest, draft.files_read)
                meta["checks"] = checks
                meta["brief"] = brief
                if current is not None:  # files it didn't read again keep the version the page was written from
                    kept = [
                        item for item in current.meta.get("files") or [] if item.get("path") not in draft.files_read
                    ]
                    meta["files"] = kept + meta["files"]
                context.guide.write_page(Page(entry.id, meta, body))
                result.written.append(entry.id)
                if context.failed_pages is not None:
                    context.failed_pages.forget(entry.id)
                unchanged = revising and not draft.sections and not draft.checks and not draft.brief
                context.progress({"step": "page_written", "id": entry.id, "title": entry.title, "revise": revising,
                                  "unchanged": unchanged, **spent})  # fmt: skip
                return
            if current is not None and any(problem.startswith(FLAGGED_PROBLEM) for problem in problems):
                request = replace(request, problems=problems)  # a flagged revision never goes back to the provider
            elif current is not None:  # the retry sees its own revision in the page, and what's wrong with it
                request = replace(
                    request, problems=problems, current_body=body, current_checks=checks, current_brief=brief
                )
            else:
                request = replace(request, problems=problems, previous_body=draft.body)
        reason = "; ".join(problems[:3])
        if context.failed_pages is not None:  # its drafts failed validation: not worth paying for again unchanged
            context.failed_pages.record(entry.id, scope_hash(context.store, entry))
    except AssistantError as error:
        reason = str(error)
        # A paid failure (counted by _paid) ran out of turns, budget or time: unchanged, it would again.
        if isinstance(error, PaidCallFailed) and context.failed_pages is not None:
            context.failed_pages.record(entry.id, scope_hash(context.store, entry))
    result.failed.append((entry.id, reason))
    context.progress({"step": "page_failed", "id": entry.id, "title": entry.title, "reason": reason})


async def _write_digest(
    context: GenerationContext, claude: Assistant, validation: ValidationContext, result: GenerationResult
) -> None:
    head = context.manifest.commit
    found = _digest_range(context, bool(result.written))
    if found is None:
        return
    context.progress({"step": "digest"})
    since, commits = found
    page_id = f"digests/{datetime.now(UTC).strftime('%Y-%m-%d')}-{head[:12]}"
    meta = {"id": page_id, "kind": "digest", "generated": True, "from_commit": since, "to_commit": head,
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"), "pages_changed": result.written}  # fmt: skip
    if not commits:
        meta["title"] = "The guide was created"
        titles = {page.id: page.title for page in context.guide.pages()}
        body = "The guide's first pages:\n\n" + "\n".join(
            f"- [{titles.get(page, page)}](/pages/{page})" for page in result.written
        )
        context.guide.write_page(Page(page_id, meta, body))
        result.digest = page_id
        return
    request = DigestRequest(context.target, commits_text(commits), fact_changes_text(context.diff), result.written,
                            context.max_highlights)  # fmt: skip
    try:
        if not _reserve(context, result, "digest"):
            raise AssistantError("the update's budget is spent")
        draft, _spent = await _paid(context, result, "digest", partial(claude.write_digest, request))
        highlights = draft.highlights if context.max_highlights else []  # none asked for: any returned are dropped
        if (
            validate_page(draft.body, None, validation)
            or validate_highlights(highlights, context.max_highlights, validation)
            or validation.scanner.scan_text(draft.title)  # the title is shown too
        ):
            raise AssistantError("the digest failed validation")
        meta["title"], body = draft.title, draft.body
        meta["highlights"] = highlights
    except AssistantError:  # a failed call is counted by _paid
        meta["title"] = f"{len(commits)} commits since the last update"
        body = "The assistant's digest couldn't be checked, so here are the commits:\n\n" + "\n".join(
            f"- {commit.subject}" for commit in commits
        )
    context.guide.write_page(Page(page_id, meta, body))
    result.digest = page_id


def _digest_range(context: GenerationContext, pages_written: bool) -> tuple[str | None, list[Commit]] | None:
    """The commits a digest covers, from the last digest to the head; None when no digest is due."""
    head = context.manifest.commit
    digests = sorted(context.guide.pages("digest"), key=lambda page: str(page.meta.get("written_at", "")))
    since = str(digests[-1].meta.get("to_commit")) if digests else context.previous_commit
    if not digests and pages_written:
        since = None  # the guide's first pages: record its creation, whatever facts-only updates came before
    if since == head or (since is None and not pages_written):
        return None
    commits = commits_between(context.mirror, since, head, context.visible, context.scanner) if since else []
    if since and not commits and not pages_written:
        return None
    return since, commits


@dataclass(frozen=True)
class PlannedPage:
    """A page the update will write, and why: "new", "outline" (its entry changed), "update" (facts in its scope
    changed in this update) or "catching_up" (they changed in an earlier update that didn't rewrite it)."""

    entry: OutlineEntry
    reason: str
    changed_now: int  # facts in its scope this update added, changed or removed
    changes: list[str] = field(default_factory=list)  # set when the page is revised: what changed since it was written


@dataclass(frozen=True)
class PageSelection:
    to_write: list[PlannedPage]
    left_for_later: list[OutlineEntry]  # beyond max_pages_per_update
    skipped: list[OutlineEntry]  # failed validation before, and their scope hasn't changed since


def select_pages(context: GenerationContext, entries: Sequence[OutlineEntry]) -> PageSelection:
    """The affected pages, in the order they are written: those this update's facts touched first, then the ones
    the reader learned, then new pages, then the backlog; within a group, the most changed facts first. A page that
    failed validation is skipped while its scope is what it was then, unless `context.retry_failed`."""
    touched = context.diff.changed_ids()
    affected: list[tuple[tuple[bool, bool, bool, int], PlannedPage]] = []
    skipped: list[OutlineEntry] = []
    for entry in entries:
        page = context.guide.read_page(entry.id)
        reason = affected_reason(entry, page, context.store)
        if reason is None:
            continue
        if context.failed_pages is not None and not context.retry_failed:
            failed_with = context.failed_pages.scope_hash(entry.id)
            if failed_with is not None and failed_with == scope_hash(context.store, entry):
                skipped.append(entry)
                continue
        changed_now = sum(1 for entity in facts_in_scope(context.store, entry) if entity.id in touched)
        kind = {"new": "new", "outline changed": "outline"}.get(reason, "update" if changed_now else "catching_up")
        rank = (not changed_now, entry.id not in context.learned, kind not in ("new", "outline"),
                -changed_fact_count(entry, page, context.store))  # fmt: skip
        affected.append((rank, PlannedPage(entry, kind, changed_now, _revision(context, entry, page, kind))))
    affected.sort(key=lambda item: item[0])
    planned = [page for _, page in affected]
    return PageSelection(planned[: context.max_pages], [page.entry for page in planned[context.max_pages :]], skipped)


def _revision(context: GenerationContext, entry: OutlineEntry, page: Page | None, kind: str) -> list[str]:
    """What changed in the page's scope since it was written, when there are few enough changes to revise it rather
    than write it again; empty for a new page, a changed outline entry, or too many changes."""
    if page is None or kind not in ("update", "catching_up") or not context.revise_max_changes:
        return []
    since = page_snapshot(context.store, page)
    changes = scope_changes(context.store, entry, since) if since is not None else []
    return changes if len(changes) <= context.revise_max_changes else []


@dataclass(frozen=True)
class PlannedWork:
    """The paid calls an update will make, counted without making any (design section 15.4)."""

    plan_calls: int
    pages_expected: int  # pages written whole
    page_calls_max: int  # each page may be written twice: the retry after a failed validation
    digest_expected: bool
    digest_possible: bool  # a digest also runs when commits came in and no page was written
    pages: list[PlannedPage] = field(default_factory=list)  # empty before the first outline
    skipped: list[OutlineEntry] = field(default_factory=list)
    revisions_expected: int = 0  # pages revised (section 6.3), each also retried once at most


def planned_work(context: GenerationContext) -> PlannedWork:
    entries = outline_entries(context.guide.read_outline())
    if not entries:  # the first outline: a plan, then as many pages as the cap allows
        return PlannedWork(1, context.max_pages, context.max_pages * PAGE_ATTEMPTS, True, True)
    stored = context.guide.read_outline()
    new_facts = set(context.diff.added_entities)
    plan_calls = int(any(fact in new_facts for fact in uncovered_facts(context.store, entries)))
    plan_calls += int(stored is not None and "paths" not in stored)
    selection = select_pages(context, entries)
    revisions = sum(1 for page in selection.to_write if page.changes)
    expected = len(selection.to_write) - revisions
    maximum = context.max_pages if plan_calls else expected  # new pages can push revisions out and take every slot

    def digest_due(pages_written: bool) -> bool:
        found = _digest_range(context, pages_written)
        return found is not None and bool(found[1])

    possible = digest_due(True) or digest_due(False)
    return PlannedWork(plan_calls, expected, maximum * PAGE_ATTEMPTS, digest_due(bool(selection.to_write)), possible,
                       selection.to_write, selection.skipped, revisions)  # fmt: skip


def facts_summary(store: FactStore, manifest: SourceManifest) -> str:
    """A rolled-up view of the facts for planning: folders, projects, decisions, packages."""
    lines = ["Top-level folders (visible files):"]
    folders: dict[str, int] = {}
    for path in manifest.files:
        parts = path.split("/")
        key = "/".join(parts[:2]) if len(parts) > 2 else parts[0] if len(parts) > 1 else "(root)"
        folders[key] = folders.get(key, 0) + 1
    lines += [f"- {folder}: {count}" for folder, count in sorted(folders.items())]
    for kind in (EntityKind.PROJECT, EntityKind.DECISION, EntityKind.PACKAGE, EntityKind.ROUTE,
                 EntityKind.TERRAFORM_MODULE, EntityKind.SWIFT_TARGET):  # fmt: skip
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
