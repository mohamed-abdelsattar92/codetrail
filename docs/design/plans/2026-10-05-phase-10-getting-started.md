# Phase 10: a generic Codetrail, documented — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the repository reads as a general tool: no target-specific names anywhere, a README that shows what Codetrail does with real screenshots, and a guide anyone can follow to install and run it.

**Architecture:** documentation, test fixtures and one screenshot script; no product behaviour changes.

**Tech stack:** Markdown, Mermaid in the README, PNG screenshots taken from Codetrail's guide to its own repository.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, section 13 (Phase 10).

## Global constraints
- No document, test, fixture or configuration names the founder's test repositories. Examples use neutral names (`shop`, `services/api`, `apps/mobile`, `infra`).
- Git history is not rewritten; only the current files change.
- Screenshots show Codetrail's guide to its own repository, taken from the running page; they contain no secrets and no personal data.
- The getting-started guide is followed end to end before it is committed.

## Review focus
- A reader with only Claude Code installed, signed in to a subscription: every step must work without Codex, Ollama or an API key.
- A reader with no subscription: the guide shows the `api_key` option and the local option.
- A reader on Linux: commands avoid macOS-only tools.
- A stale README claim: every command shown is run before the README is committed.
- Large images: each screenshot under 400 KB.

---

### Task 1: neutral names everywhere
**Files:** every file `git grep -il` finds for the test repository's name, among them `README.md`, `AGENTS.md`, the design, the brainstorm decisions, the phase plans, the ADRs and their index, the security checklist, `tests/`, `tools/`, `lefthook.yml`, `.mise.toml`, `pyproject.toml`, `commitlint.config.mjs`, `.github/workflows/ci.yml`, `src/codetrail/repo/rules.py`.
- [ ] Replace each mention with neutral wording ("a large monorepo", "the first test repository") or a neutral example name; test fixtures keep their meaning.
- [ ] `git grep -il <name>` returns nothing; `just ci` passes.
- [ ] Commit `docs(docs): describe Codetrail as a general tool, with neutral examples`.

### Task 2: the getting-started guide
**Files:** `docs/getting-started.md`.
- [ ] Sections: what you need (git, uv, gitleaks; one provider: Claude Code, Codex, or Ollama or LM Studio); install (`uv tool install` from a clone or a tag); choose and sign in to a provider (`codetrail providers`); add a repository (`codetrail target add`); check what Codetrail sees (`codetrail files`); the first update with its estimate; open the page (`codetrail serve`); ask, learn, and keep up; configuration reference; uninstalling; troubleshooting.
- [ ] Follow it on this machine from a clean configuration folder (`XDG_CONFIG_HOME` and `XDG_DATA_HOME` in the scratchpad), against Codetrail's own repository.
- [ ] Commit `docs(docs): add the getting-started guide`.

### Task 3: screenshots and the README
**Files:** `docs/images/*.png`, `README.md`.
- [ ] Build Codetrail's guide to itself (one update on the founder's subscription, after its estimate), serve it, and capture: the home page, an area page with its diagram, a concept page with its checks, the update estimate dialog, and an answer streaming.
- [ ] README: what Codetrail is, how it works (diagram), the screenshots, providers and sign-in, costs and estimates, security in brief, the status of every phase, links to the getting-started guide and the design.
- [ ] Commit `docs(docs): show Codetrail with screenshots of its own guide`.
