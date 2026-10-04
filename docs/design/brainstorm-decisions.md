# Brainstorm decisions

Decisions made with the founder on 4 October 2026, in a Claude Code session started in `hamesh-monorepo`. This file carries that conversation into this repository so the design can continue here without repeating it. It records decisions and their reasons; the design itself (architecture, data flow, security model, phases) is not written yet.

## The problem

`hamesh-monorepo` has grown past what the founder can follow: about 1,300 tracked files, 24 ADRs, a PRD in sections, and agents merging features into `develop` every day. The founder is falling behind on its architecture, design patterns and tools.

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
| 4 | General or Hamesh-specific | **General.** Codetrail works on any repository through plug-in extractors per stack and a configuration per target repository. Hamesh is the first target. | The founder wants to reuse it on other projects. Hamesh alone gives the extractor interface several real callers from the start (Python, Swift, Terraform, OpenAPI, ADRs, commit bodies). |
| 5 | Learning modes | **All four:** concept pages tied to real code; guided paths; checks on understanding graded by Claude; progress tracking, including what is new since the last visit and which learned pages went stale. | The founder learns all four ways. They will be built in phases, each phase usable on its own. |
| 6 | When the guide updates | **On demand, plus a free "you're behind" signal.** The founder runs an update from the page; meanwhile the page shows, from git alone, how many merges landed since the last update and which areas they touched. | The signal costs nothing; Claude is called only when the founder decides an update is worth it. Rejected: after every merge (noisy, costly with agents merging often), on a schedule. |
| 7 | Language | **English guide; the bridge answers in the language it is asked in.** Saved answers keep their language. | The sources are English, so quotes need no translation; asking in Arabic still works when a concept needs it. Rejected: an Arabic or fully bilingual guide (double the cost for one reader). |
| 8 | Approach | **Hybrid: facts for structure, Claude for meaning.** Plug-in extractors parse the repository into a fact store. Diagrams are drawn from facts, never by Claude. Claude writes concept pages, paths, digests and checks from the facts and diffs, and may read files, read-only, for the "why". | Learning wrong things is costly, so structure must be grounded; the "why" needs Claude. Facts also tell an update exactly which pages to rewrite, and each page records the facts and files it was built from, which gives staleness tracking. Rejected: agent-first (Claude re-explores each time: invented diagram edges, costlier updates, weak staleness) and facts only (no explanations, misses need C). |
| 9 | Stack | **Python with uv; FastAPI serving the page and the bridge; SQLite for facts and learning state; the knowledge base as Markdown files with front matter in their own git repository; tree-sitter for parsing; the Claude Agent SDK for Python for generation and the bridge; Mermaid rendered in the browser.** | All familiar to the founder from Hamesh. The TypeScript equivalent would work but offers nothing extra here. |
| 10 | Where Codetrail lives | **This repository,** developed locally, with nothing in `hamesh-monorepo`. | The founder wants no Codetrail code in Hamesh. Local rather than a cloud environment because Codetrail reads a local checkout, runs Claude Code under the founder's login, and is tested in the founder's own browser. |

## Principles that follow from the decisions

- **Documented versus inferred.** Most repositories have no ADRs and no "why" in their commits. The guide marks documented rationale (quoted from an ADR, decision log or commit, with a link) apart from inferred rationale (Claude's reading of the code). Nobody should learn a guess as a decision.
- **Codetrail never writes to a target repository.** Each target's knowledge base lives outside it, in Codetrail's data folder or a repository the founder chooses.
- **Secrets stay out.** When Codetrail runs Claude inside a target repository, that repository's own Claude Code hooks (such as Hamesh's secret-read blocker) don't necessarily apply. Codetrail needs its own guard: extractors and Claude's read tools skip git-ignored files and secret patterns (`.env*`, keys, credential files).
- **The bridge is locked down.** It listens on `127.0.0.1` only, checks the `Host` and `Origin` headers (against DNS rebinding and other sites in the browser), requires a per-session token, and gives Claude read-only tools (Read, Grep, Glob): no Bash, no Edit, no Write.

## Parts of the system

1. **Extractors and the fact store:** deterministic facts from the repository.
2. **Knowledge-base generator:** Claude writes and updates concept pages, paths, digests and checks from the facts.
3. **Web page:** browsing, diagrams, the "you're behind" signal, progress.
4. **Bridge:** live questions, grading checks, "save to guide".
5. **Learning state:** what was read, which checks passed, what went stale.

## Open questions for the design

- Default home of a target's knowledge base: Codetrail's data folder, or a private git repository per target?
- Does the Claude Agent SDK run under the founder's Claude Code login, or does it need an API key? `claude -p` uses the existing login. Check before choosing.
- The fact model: what a fact is, how it names its source files, and how a page records the facts it was built from.
- The extractor interface, and which extractors Hamesh needs first.
- How the page is built: server-rendered pages from FastAPI, or a small front end.
- How checks are written, graded and stored; how staleness resets a "learned" mark.
- Phase order. A suggestion to start from: facts; then concept pages and the page; then the bridge; then paths, checks and progress.

## Where to pick up

The brainstorm has finished asking questions and choosing an approach. Next: present the design section by section (architecture and data flow, the fact model, extractors, generation, the page and bridge with their security model, learning state, error handling, testing, phases), get the founder's approval on each, then write the spec in `docs/design/`, then the implementation plan.
