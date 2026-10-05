# Phase 9: cost estimates before paid work — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** no paid action starts without its estimate: an update asks first, in the page and on the command line; questions and graded checks show theirs on the button.

**Architecture:** `assistant/estimate.py` turns history (Phase 8's `assistant_calls`), starting values and prices into an estimate per call and per update. The page fetches an update's estimate, shows it in a dialog, and starts the update with the estimate's id; the server refuses an update without a fresh id from the same session. The command line prints the same estimate and asks.

**Tech stack:** Python, FastAPI, Jinja2, the page's one JavaScript file, pytest.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 15.3 and 15.4.

## Global constraints
- Estimates never call a provider.
- Prices are configuration (`[prices]`, USD per million tokens); an unpriced model shows tokens only, with a note on how to add its price.
- An estimate id is `secrets.token_urlsafe(16)`, bound to the session, single-use, valid `server.estimate_ttl_seconds`.
- New interface strings go through gettext; `just catalogs` keeps the template current.

## Review focus
- An update started from the page without an estimate, with another session's estimate, with a used one, or with an expired one: refused.
- No history at all, and a model with no price: the estimate still renders, with a clear note.
- `codetrail update` run without a terminal (a script or cron) and without `--yes`: refused, with how to proceed.
- A plan usage reading days old: shown with its age, never as current.
- A `--facts-only` update: no estimate and no question, since it calls nothing.

---

### Task 1: prices, starting values and per-call estimates
**Files:** `src/codetrail/config.py` (`[estimates]`, `server.estimate_ttl_seconds`), `src/codetrail/assistant/estimate.py`; test `tests/unit/test_estimate.py`.
**Interfaces:**
```python
@dataclass(frozen=True)
class CallEstimate:
    kind: str; provider: str; model: str
    input_tokens: int; output_tokens: int
    cost_usd: float | None      # None when the model has no price

def estimate_call(connection, kind, provider, model, settings: EstimateSettings, prices) -> CallEstimate
```
- [ ] Tests: starting values with no history; the median once three calls exist; only the last `history_size` count; other kinds, providers and models don't mix; `local` costs zero; an unpriced model gives `None`.
- [ ] Commit `feat(claude): estimate each call from history, starting values and prices`.

### Task 2: an update's estimate
**Files:** `src/codetrail/assistant/estimate.py`, `src/codetrail/generate/run.py` (a `planned_work(context)` that counts, without calling anything: whether a plan call is needed, the affected pages, and whether a digest is due); test `tests/integration/test_update_estimate.py`.
**Interfaces:**
```python
@dataclass(frozen=True)
class UpdateEstimate:
    calls: list[tuple[CallEstimate, int, int]]   # each kind's estimate, expected count, maximum count
    expected_tokens: int; maximum_tokens: int
    expected_usd: float | None; maximum_usd: float | None
    providers: list[ProviderStatus]; plan_usage: list[PlanUsageReading]
```
- [ ] Tests: the first update (no outline: plan plus the page cap); an update with two affected pages and no new facts (no plan call); a digest only when commits came in; the maximum is capped by `max_pages_per_update` and `max_budget_usd_per_update`.
- [ ] Commit `feat(generate): estimate an update before it starts`.

### Task 3: the command line asks
**Files:** `src/codetrail/cli.py`; test `tests/e2e/test_update_command.py`.
- [ ] `codetrail update` prints the estimate and asks `Continue? [y/N]`; `--yes` skips the question; without a terminal and without `--yes` it refuses with exit code 2; `--facts-only` neither prints nor asks. After the run it prints actual tokens and cost.
- [ ] Commit `feat(generate): show the update's estimate and ask before going on`.

### Task 4: the page's dialog and the estimate id
**Files:** `src/codetrail/web/app.py` (`GET /update/estimate`, `POST /update` requiring `estimate_id`), `src/codetrail/web/templates/home.html`, `src/codetrail/web/static/page.js`, `page.css`, catalogs; tests `tests/api/test_update_estimate.py`.
- [ ] Tests: the estimate's JSON; `POST /update` with no id, a wrong id, another session's id, an expired id or a reused id is refused (428); with a fresh id the update starts; the security headers and token rules still apply.
- [ ] The dialog lists each call's provider, model, sign-in, expected and maximum tokens and dollars, and the plan usage with its age; Proceed and Cancel.
- [ ] Commit `feat(web): confirm an update's estimate before it starts`.

### Task 5: estimates on the Ask and Check buttons
**Files:** `src/codetrail/web/app.py`, templates for the bridge and checks, catalogs; tests in `tests/api/`.
- [ ] Each Ask and "Check my answer" button shows `~N tokens · ≤ $X` (or "free" for local, "tokens only" when unpriced); the limit is the configured per-call budget.
- [ ] After an answer or a grade, its actual tokens and cost show beside it.
- [ ] Commit `feat(bridge): show each question's and check's estimate on its button`.
