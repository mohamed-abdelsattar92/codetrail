# 0021. Count the "you're behind" signal from the commit the guide last covered

- Status: proposed
- Date: 2026-11-14
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder approved the fix on 14 November 2026
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 8.5, 23.2 ("Which commit") and 24 (the team page's cards)

## Context
Section 8.5 counts the merges and commits since "the last update's commit", which the code reads as the latest facts snapshot. A facts-only update (`codetrail update --facts-only`, or the free part of a team catch-up) records a new snapshot without writing the guide. So after one, the home card says "Up to date" and "The guide matches the branch" while the guide hasn't taken in the commits it skipped.

The team page already had a workaround: a repository the last catch-up missed counts from its newest digest's end (`to_commit`), because the catch-up's free part moved its snapshot. A facts-only update run by hand wasn't covered, and the home page never used it.

Facts:
- Every paid update that finds commits since the last digest writes a digest ending at its commit, even when it writes no page and even when every commit touched only excluded files (section 3.3: such a commit still appears, with no files listed). The first guide's digest ends at its commit too. So the newest digest's `to_commit` is the commit the guide last covered.
- Generation already catches up after a facts-only update: each page is compared with the snapshot it was written at (section 6.3), not with the previous snapshot.
- `behind(paths, name, since)` already measures from `since` when it is in the branch's history, and from the snapshot otherwise.

## Decision drivers
- The signal must not say "Up to date" while the guide is behind the branch.
- The home page and the team page must agree.
- No new stored state if what is stored already answers it.

## Options considered
### Option A: count from the newest digest's end, on both pages
- Good, because it is the commit the guide last covered, already written by every paid update, and the team page already trusts it.
- Good, because it needs no new file, column or migration.
- Bad, because a guide whose digests were deleted by hand counts from the snapshot again.

### Option B: record the covered commit in its own file in the guide on every paid update
- Good, because it doesn't depend on digests.
- Bad, because it repeats what the newest digest records; tried and dropped while writing the fix.

### Option C: keep counting from the snapshot, and say "facts only" on the card
- Good, because it changes nothing stored or measured.
- Bad, because the count would still read 0 while the guide is behind.

## Decision
Option A. The home card and every team card count from the newest digest's `to_commit` when it is in the branch's history, and from the latest snapshot otherwise (no digest yet, or history rewritten). A facts-only update then leaves "N commits behind" until a paid update catches the guide up.

## Consequences
- After `--facts-only` or a catch-up's free part, the home and team cards show what the guide hasn't taken in, with the update button.
- The team page no longer needs the last catch-up's "missed" list to pick where to count from; it still uses it to say why a card wasn't caught up.
- The coding agents' map still describes the latest snapshot's commit, which can now be ahead of where the signal counts from.
- Revisit if digests stop being written on every paid update with new commits.

## Changes required
- [x] Design section 8.5: count from the guide's newest digest's end.
- [x] Design section 23.2 ("Which commit"): the map's commit is no longer the one the signal counts from.
- [x] Design section 24: every card counts from its newest digest's end, not only a missed one.
- [x] Code: `GuideRepository.covered_commit()`, used by the home page's signal and the team page's rows.
