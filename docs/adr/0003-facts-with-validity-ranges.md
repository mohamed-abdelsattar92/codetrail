# 0003. Store each fact once with a validity range over snapshots

- Status: accepted (by the founder, 5 October 2026)
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code, from the design session of 5 October 2026
- Design: `docs/design/2026-10-05-codetrail-design.md` section 4.2; brainstorm decision 8

## Context
- Every update takes a snapshot of the facts and needs the difference from the previous one, to find the pages to rewrite and to write the digest.
- Hamesh has about 1,300 tracked files, a few thousand facts. The founder wants the design to stay workable on repositories of around 100,000 files, roughly a million relations per snapshot.
- Storing a full copy of the facts per update grows with repository size times the number of updates: gigabytes at that scale.
- The fact store's schema is costly to change once guides and learning state depend on it.

## Decision drivers
1. Storage that grows with change, not with repository size.
2. A simple diff between two snapshots.
3. Standard SQL in the standard library's `sqlite3`, no extra framework.

## Options considered
### Option A: one row per fact version, with `first_seen` and `last_seen` (chosen)
- Good, because an update writes only what changed: new rows for new and changed facts, a closed range for removed ones.
- Good, because the diff for a snapshot is a query on the ranges that open or close at it.
- Good, because it is a standard pattern (a type 2 history table).
- Bad, because updates must close and open rows carefully, and queries for "current" facts filter on `last_seen IS NULL`.

### Option B: a full copy per snapshot
- Good, because each snapshot is self-contained and simple to query.
- Bad, because storage grows with repository size times updates, and diffing compares two full sets.

### Option C: only the current facts, plus a change log per update
- Good, because current queries are simplest.
- Bad, because past states can't be reconstructed without replaying the log, and two structures must stay consistent.

## Decision
Option A. Entities and relations each have `first_seen` and `last_seen` snapshot ids; `last_seen` is `NULL` while current. A fact's hash covers its kind, key and attributes; its sources are stored with it and refreshed in place while the version is current.

## Consequences
- Easier: storage proportional to churn; past states queryable; cheap diffs.
- Harder: update logic and its tests must cover opening, closing and unchanged facts.
- Revisit if querying history turns out to need more than ranges, for example per-snapshot sources.

## Changes required
- [ ] `facts`: the schema as the first migration, with tests for added, changed and removed facts (Phase 2).
- [x] The founder accepted this ADR on 5 October 2026.
