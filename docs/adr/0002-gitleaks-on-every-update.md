# 0002. Scan the materialized sources with gitleaks on every update, and exclude what it flags

- Status: proposed
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code, from the design session of 5 October 2026
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 3.4, 10 and 12; non-negotiable 1 in AGENTS.md

## Context
- Path rules (built-in secret patterns and ignore files) exclude files by name. They can't see a secret committed inside an ordinary file, such as a key pasted into a settings module.
- Anything in `source/` can reach Claude, and through Claude the guide, whose git history keeps it.
- gitleaks is already pinned by mise for this repository's pre-commit hook (decision 11). It is a single Go binary, licensed MIT, and since 8.19 scans a directory without git (`gitleaks dir`), with values redacted from its report (`--redact`).
- Codetrail is installed for daily use with `uv tool install`, which can't install a Go binary; gitleaks must be on the founder's `PATH`.

## Decision drivers
1. Secrets never reach Claude or the guide (non-negotiable 1).
2. Fail closed: a missing scanner must not mean an unscanned update.
3. No new service, and as little new tooling as possible.

## Options considered
### Option A: gitleaks on every update, failing closed (chosen)
- Good, because it catches committed secrets that path rules can't, with a maintained rule set.
- Good, because the founder already has it through mise.
- Bad, because Codetrail now needs an external binary at runtime, and an update fails when it's missing.
- Bad, because false positives exclude ordinary files; the update summary names them, and the founder can't re-include a flagged file except by changing it.

### Option B: path rules only
- Good, because there is nothing to install.
- Bad, because a secret in an ordinary file reaches Claude and the guide.

### Option C: a secret detector written in Python
- Good, because it would install with Codetrail.
- Bad, because a homemade rule set is weaker than gitleaks', and it's code to maintain.

## Decision
Option A. Each refresh runs `gitleaks dir` over the new and changed files in `source.next/` with `--redact` and a JSON report, removes flagged files, and records their blob hashes so they stay excluded while unchanged. The summary lists them by path and rule, never by value. If gitleaks is missing or fails, the refresh fails.

## Consequences
- Easier: committed secrets are caught before Claude or the page can see them.
- Harder: gitleaks becomes a runtime requirement, checked at startup with a clear message; its version is pinned in `.mise.toml`.
- Revisit if false positives exclude files the guide needs, which would call for an allowlist in the target's configuration and a new ADR.

## Changes required
- [ ] `README.md`: gitleaks listed as a requirement for running Codetrail (Phase 1).
- [ ] `repo`: the scan, with tests for flagged, unflagged and missing-gitleaks cases (Phase 1).
