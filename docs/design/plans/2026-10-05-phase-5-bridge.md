# Phase 5: Bridge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From any page, the reader asks Claude a question; the answer streams into the page in the reader's language, read-only over `source/`, and can be saved into the guide.

**Architecture:** `Claude.answer(QuestionRequest)` streams text chunks and ends with the files read and the cost. `bridge` is a FastAPI router: `POST /bridge/questions` streams newline-delimited JSON events (`text`, then `done` with the answer's id and its rendered HTML, or `error`); answers are kept in memory for the session; `POST /bridge/answers/{id}/save` validates the answer (an unverifiable documented quote becomes an inferred block) and commits it to `answers/` in the guide, under the target's lock.

**Tech Stack:** Claude Agent SDK (partial messages for streaming), FastAPI `StreamingResponse`, page.js `fetch` with a streamed body.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, section 7.3 and 7.4.

## Global Constraints
- The bridge's routes sit behind the same middleware: session, Origin and token on every POST.
- Claude answers with Read, Grep and Glob over `source/` through the tool guard, no settings, the configured `max_turns`, a cost limit per answer, and the target's `answer` model.
- One question in flight per session; a question over `bridge.max_question_chars` is refused; the language is a validated installed code; the page id must exist.
- Closing the page cancels Claude's run. A failure mid-answer ends the stream with an error event, and nothing is kept.
- Answers render through the same Markdown renderer as pages (raw HTML off).
- Saving refuses while an update holds the target's lock.

## Review Focus
1. A question asking Claude to read `~/.ssh` or run a command: the guard refuses; the answer says it couldn't.
2. Two questions at once from one session: the second is refused with 429.
3. A saved answer quoting text that isn't in the cited lines is saved with that block turned inferred, never as documented.
4. An answer containing `<script>` or a `javascript:` link is inert in the streamed text (shown as text) and in the rendered HTML.
5. Saving while an update runs returns 409 and writes nothing.

## Tasks
1. `QuestionRequest`, `AnswerChunk`, `AnswerResult`; `Claude.answer` in the protocol, the fake (scripted chunks or an error) and the adapter (`include_partial_messages`, text deltas, no output schema). Settings: `[bridge] max_question_chars = 4000, max_turns = 20, max_budget_usd = 1.0` (global) and `[models] answer` (target). Tests: the adapter's options; the fake.
2. `src/codetrail/bridge.py`: the router, the in-memory answers, the one-in-flight lock, the NDJSON stream, cancellation, error events. Tests through the test client with the fake.
3. Saving: `answers/<date>-<slug>` pages with question, language, snapshot, files and blobs; documented blocks that fail verification become inferred; committed under the lock. Tests.
4. The question box on every page (page.js streaming, the Save button), strings in the catalog; then ask Claude a real question about Hamesh in the browser, save the answer, check Hamesh unchanged; documents; review; finish.
