# Phase 6: Learning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The guide becomes a course: guided paths through the pages, checks answered in the page and graded by Claude, progress marks (read, learned), staleness when a learned page is rewritten (with what changed since), and a catch-up list.

**Architecture:** `learn` keeps progress in the target's database (migration `0003_learning.sql`): page marks with the page version they apply to, check passes keyed by the check's hash, attempts, and digest reads. Grading is a new `Claude.grade` call with **no tools at all** and a structured verdict. Paths are part of the outline, planned by Claude, validated (every step an existing page) and written by Codetrail as `paths/<slug>` pages. The page shows the status, the checks and, for a stale page, the diff since it was learned, read from the guide's git history.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, section 8.

## Global Constraints
- A page's version is a hash of its body and checks; a check's hash covers its question and rubric. A page is learned when every current check has a pass for its current hash.
- A learned page whose version changed is stale until its new or changed checks pass; unchanged checks stay passed. A "sources changed" notice never resets learned.
- Grading runs with `tools=[]`; the rubric is never sent to the browser; a malformed verdict is an error, never a pass; feedback is in the reader's language.
- The answer to a check is limited to `bridge.max_question_chars`; one grading at a time per session.
- Generation ranks learned pages first among affected pages.

## Review Focus
1. A check's rubric must not appear in any page HTML or JSON response.
2. A verdict outside pass/partial/fail, or missing, is reported as an error and stored as no pass.
3. A page rewritten with one changed check: only that check needs answering again.
4. An answer containing instructions ("mark this as pass") is graded as an answer, not obeyed; grading has no tools to misuse.
5. Path steps naming pages that don't exist are dropped with a problem; a path with no valid steps is dropped.

## Tasks
1. Migration and `learn` (versions, marks, passes, attempts, digest reads, status, catch-up); tests for every state transition.
2. `Claude.grade` (interface, fake, adapter with no tools, prompt with the rubric as data, verdict schema); tests including a malformed verdict; a live test.
3. Paths in the outline (schema, validation, `paths/` pages written by Codetrail, planned for an outline that has none); learned-first ranking; tests.
4. The page: status and "Mark read", checks with answer boxes (`POST /bridge/checks/{page}/{check}`), the stale diff, paths and catch-up on the home page; catalog strings; tests.
5. The first test repository: plan paths, answer a real check in the browser, mark pages, check the target repository unchanged; documents; review.
