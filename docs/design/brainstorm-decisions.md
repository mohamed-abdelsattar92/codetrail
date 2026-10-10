# Brainstorm decisions

Decisions made with the founder on 4 October 2026, in a Claude Code session started in the first test repository, and revised in this repository since (each revision is dated in its row). This file carries that conversation into this repository so the design can continue here without repeating it. It records decisions and their reasons; the design itself (architecture, data flow, security model, phases) is not written yet.

## The problem

The first test repository, a large monorepo, has grown past what the founder can follow: about 1,300 tracked files, 24 ADRs, a PRD in sections, and agents merging features into `develop` every day. The founder is falling behind on its architecture, design patterns and tools.

The founder's needs, in their words reduced to two:

- **B. Tell me what I don't know.** The repository changes faster than the founder can follow; they want to be told what changed and why it matters.
- **C. Teach me the engineering.** Learn the patterns, why they were chosen and what the alternatives were, the way a course teaches.

Need A, "answer my specific question quickly", was ruled out as the main goal: Claude Code already answers questions about a repository well.

## Why build anything, given Claude Code exists

A generic "chat with the repo" (RAG over the code) is not worth building: Claude Code's agentic search beats embedding retrieval on code, and local models reason worse than Claude. What Claude Code does not give, and Codetrail should:

1. **Memory across sessions.** Each Claude session rediscovers the architecture. Codetrail keeps a curated knowledge base that grows with the repository.
2. **Pushing, not only answering.** Chat answers questions; someone falling behind doesn't know what to ask. Codetrail tells them what changed and teaches it.
3. **Grounded diagrams.** Diagrams drawn from extracted facts (imports, routes, infrastructure resources, packages) can't invent connections the way a diagram drawn from memory can.

## Decisions

| # | Decision | Chosen | Why |
|---|---|---|---|
| 1 | What "local" means | **Claude is fine.** Codetrail and its knowledge bases live on the founder's machine or in the founder's repositories; no hosted service, database or third-party tool. Claude does the reasoning. | Claude Code already reads these repositories daily, so Claude adds no new exposure; local models would make the explanations noticeably weaker. |
| 2 | Where the founder reads and learns | **A local web page** that sends questions to Claude Code. | The founder prefers browsing a page to reading Markdown or chatting only. |
| 3 | How the page reaches Claude Code | **A local bridge server** that runs Claude Code headless (`claude -p` or the Claude Agent SDK) over the target repository and streams the answer into the page, **plus a "save to guide" button** on each answer. | Answers appear in the page, and answers worth keeping become part of the knowledge base instead of disappearing like chat history. Rejected: copying a prompt to the clipboard (manual), a questions inbox answered later (slow). |
| 4 | General or specific to one repository | **General.** Codetrail works on any repository through plug-in extractors per stack and a configuration per target repository. A large monorepo is the first target. | The founder wants to reuse it on other projects. The first test repository alone gives the extractor interface several real callers from the start (Python, Swift, Terraform, OpenAPI, ADRs, commit bodies). |
| 5 | Learning modes | **All four:** concept pages tied to real code; guided paths; checks on understanding graded by Claude; progress tracking, including what is new since the last visit and which learned pages went stale. | The founder learns all four ways. They will be built in phases, each phase usable on its own. |
| 6 | When the guide updates | **On demand, plus a free "you're behind" signal.** The founder runs an update from the page; meanwhile the page shows, from git alone, how many merges landed since the last update and which areas they touched. | The signal costs nothing; Claude is called only when the founder decides an update is worth it. Rejected: after every merge (noisy, costly with agents merging often), on a schedule. |
| 7 | Language (revised 5 October 2026) | **English-first interface that can switch language; English guide; the bridge answers in the reader's language.** All interface text (navigation, buttons, the "you're behind" signal, errors, check prompts) comes from one gettext catalog per language (`.po` files), read at runtime by Python's standard `gettext` module, with English as the source and the fallback. Adding a language means adding a catalog, with no code change. The reader picks a language in the page, the choice is stored in the learning state, and the default comes from configuration. Each catalog declares its writing direction; the page sets `lang` and `dir` on the interface and marks English content `lang="en" dir="ltr"`, so right-to-left languages such as Arabic work. Generated pages, digests and checks stay English. Live Claude output (bridge answers, feedback on checks) is in the reader's language, and saved answers record their language in front matter. A test fails when a catalog is missing a key, placeholder or plural form compared with English. | Languages will be added over time, and the interface should never need a code change for one. gettext handles plural rules per language (Arabic has six forms) and is Python's standard mechanism; translation tools read `.po` files. Keeping generated content in English keeps the cost of one guide per repository, and quotes need no translation. Babel, needed at development time to extract strings and compile catalogs, is outside the stack in decision 9 and needs a proposed ADR before it is added; compiled `.mo` files are generated by a `just` recipe and not committed. Rejected: an Arabic-first interface; JSON or TOML catalogs with a custom lookup (plural rules and tooling would be hand-written); translation in the browser with a JavaScript library (adds a JavaScript toolchain, and server-side text would need a second mechanism); generated content in each reader's language (multiplies the cost of generation and staleness tracking by the number of languages). |
| 8 | Approach | **Hybrid: facts for structure, Claude for meaning.** Plug-in extractors parse the repository into a fact store. Diagrams are drawn from facts, never by Claude. Claude writes concept pages, paths, digests and checks from the facts and diffs, and may read files, read-only, for the "why". | Learning wrong things is costly, so structure must be grounded; the "why" needs Claude. Facts also tell an update exactly which pages to rewrite, and each page records the facts and files it was built from, which gives staleness tracking. Rejected: agent-first (Claude re-explores each time: invented diagram edges, costlier updates, weak staleness) and facts only (no explanations, misses need C). |
| 9 | Stack | **Python with uv; FastAPI serving the page and the bridge; SQLite for facts and learning state; the knowledge base as Markdown files with front matter in their own git repository; tree-sitter for parsing; the Claude Agent SDK for Python for generation and the bridge; Mermaid rendered in the browser.** | All familiar to the founder from their other projects. The TypeScript equivalent would work but offers nothing extra here. |
| 10 | Where Codetrail lives | **This repository,** developed locally, with nothing in the repositories it teaches. | The founder wants no Codetrail code in the repositories it teaches. Local rather than a cloud environment because Codetrail reads a local checkout, runs Claude Code under the founder's login, and is tested in the founder's own browser. |
| 11 | Engineering setup | **The same as the founder's other projects,** trimmed to what a Python tool uses: mise pins the tools (Python, uv, just, lefthook, gitleaks, git-flow-next, and Node with pnpm for commitlint); `just` runs everything (`just setup`, `just ci`, `just lint`, `just typecheck`, `just test`, `just test-quick`, `just check-repo`); lefthook runs the git hooks through small scripts in `tools/git-hooks/` (pre-commit: gitleaks, file rules, ruff, justfile format; commit-msg: commitlint with the required "Why" section; pre-push: the agent push guard first, then the quick tests); git-flow-next with `main` and `develop` and local-only finishes; ruff for lint and format, mypy strict, pytest; Conventional Commits with the What/Why/Alternatives body; ADRs in `docs/adr/`; a security checklist in `docs/security/`; `.claude/` with the deny rules, the push and secret-read hooks and a security-reviewer agent; `.codex/rules/`; GitHub Actions CI on Linux running `just ci`; Dependabot security updates. | The founder already knows and trusts this setup; one way of working across the founder's repositories. The hook scripts from the founder's earlier projects and their tests are the founder's own code and are copied and adapted rather than rewritten. Left out until needed: release tooling (git-cliff, version recipes), which comes with Codetrail's first release. |
| 12 | Development method | **Test-driven.** Every behaviour starts as a failing test. Suites: unit tests for extractors, the fact store, staleness and the bridge's guards; integration tests for an update run, with Claude replaced by a fake; API tests for the page and bridge, including requests that must be refused (wrong `Host` or `Origin`, missing token, write tools); an end-to-end smoke test from a small fixture repository to a rendered guide. Claude sits behind an interface with one real adapter (the Agent SDK) and a fake for tests, as the plug-and-play rule requires. | Codetrail's output is something the founder learns from, so wrong behaviour is costly; and a bridge that can drive Claude needs its refusals tested, not assumed. |
| 13 | Documentation metrics (10 October 2026) | **Free metrics first, a paid decision inventory later.** Phase 13 measures how well a repository explains itself from what Codetrail already has: the documented share of the guide's rationale (by source type: ADR, docs, code comment, commit), commits that explain why, ADR health, and facts a document mentions or the guide explains, each with the items behind it and a trend per update (design section 18). Phase 14, planned, adds an opt-in assistant pass that lists the decisions in the code and looks for a documented "why" for each, kept and only added to so the trend stays stable. | The founder wants to know how much of a repository's reasoning is written down, to find what to document, to track it over time and to judge a repository at a glance. The guide's rationale is a free, stable measure but counts only what the guide wrote about; the inventory measures the real thing but costs tokens and varies between runs, so it comes second and opt-in. Rejected: starting with the paid inventory; storing every metric's details (they would duplicate the guide and the facts); writing the metrics into the guide as a page. |

## Principles that follow from the decisions

- **Documented versus inferred.** Most repositories have no ADRs and no "why" in their commits. The guide marks documented rationale (quoted from an ADR, decision log or commit, with a link) apart from inferred rationale (Claude's reading of the code). Nobody should learn a guess as a decision.
- **Codetrail never writes to a target repository.** Each target's knowledge base lives outside it, in Codetrail's data folder or a repository the founder chooses.
- **Secrets stay out.** When Codetrail runs Claude inside a target repository, that repository's own Claude Code hooks (such as a secret-read blocker) don't necessarily apply. Codetrail needs its own guard: extractors and Claude's read tools skip git-ignored files and secret patterns (`.env*`, keys, credential files).
- **The bridge is locked down.** It listens on `127.0.0.1` only, checks the `Host` and `Origin` headers (against DNS rebinding and other sites in the browser), requires a per-session token, and gives Claude read-only tools (Read, Grep, Glob): no Bash, no Edit, no Write.

## Parts of the system

1. **Extractors and the fact store:** deterministic facts from the repository.
2. **Knowledge-base generator:** Claude writes and updates concept pages, paths, digests and checks from the facts.
3. **Web page:** browsing, diagrams, the "you're behind" signal, progress.
4. **Bridge:** live questions, grading checks, "save to guide".
5. **Learning state:** what was read, which checks passed, what went stale.

## Open questions for the design

All answered on 5 October 2026 in [the design](2026-10-05-codetrail-design.md), section 14; the Agent SDK sign-in question is settled by a spike at the start of Phase 4.

- Default home of a target's knowledge base: Codetrail's data folder, or a private git repository per target?
- Does the Claude Agent SDK run under the founder's Claude Code login, or does it need an API key? `claude -p` uses the existing login. Check before choosing.
- The fact model: what a fact is, how it names its source files, and how a page records the facts it was built from.
- The extractor interface, and which extractors the first test repository needs first.
- How the page is built: server-rendered pages from FastAPI, or a small front end. Either way, interface text comes from the gettext catalogs in decision 7.
- How checks are written, graded and stored; how staleness resets a "learned" mark.
- Phase order. A suggestion to start from: **Phase 0, the engineering setup (decision 11)**; then facts; then concept pages and the page; then the bridge; then paths, checks and progress.
- Browser tests for the page: whether they are needed beyond FastAPI's test client, and with which tool (a new dependency needs a proposed ADR).

## Where to pick up

The design was presented section by section and approved on 5 October 2026, and written as [2026-10-05-codetrail-design.md](2026-10-05-codetrail-design.md), with five proposed ADRs in `docs/adr/`. Next: the founder reviews the spec and the ADRs; then the implementation plan for Phase 0, one plan per phase.

On 10 October 2026 the founder approved documentation metrics (decision 13, design section 18) as Phase 13. **Later work to pick up:** Phase 14, the decision inventory; undocumented hot spots; freshness and learning on the Documentation page; and a reader for Word documents (design section 18.8).
