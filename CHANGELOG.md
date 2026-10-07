# Changelog

Every notable change to Codetrail, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- A security policy, [SECURITY.md](SECURITY.md): how to report a vulnerability privately, and what counts as one.
- Contribution rules in [CONTRIBUTING.md](CONTRIBUTING.md): git-flow, every change through a pull request, simple code, tests first, the changelog, and how a release reaches `main` through a pull request.

### Fixed
- A target that uses Codex without allowing it is refused with that reason first, whatever is installed; before, a missing Claude Code was reported instead.

## [1.0.0] - 2026-10-07

The first public release. Release notes: [docs/releases/v1.0.0.md](docs/releases/v1.0.0.md).

### Added

**Reading a repository**
- Targets: `codetrail target add` and `codetrail target remove`, and `codetrail files` to list exactly what Codetrail can see. Codetrail reads only committed files, from its own mirror, and never writes to the repository it teaches.
- Exclusions before anything else: built-in secret patterns, the repository's ignore rules, your own exclusions, and a gitleaks scan of every file. What's excluded never reaches an extractor, the assistant or the page (except Codex, for a target that opts in to it).
- Facts from deterministic extractors: Python, TypeScript, JavaScript and Astro, OpenAPI, Terraform, Swift packages, GitHub Actions deploy evidence, and architecture decision records. Facts are kept per snapshot with validity ranges, so every change can be diffed.
- The system pass: the repository's services, apps, libraries, contracts and infrastructure, and how they connect, from facts alone.

**The guide**
- Area and concept pages, guided paths, digests and checks, written by your assistant from the facts and kept in the guide's own git repository.
- Grounded rationale: quotes from ADRs, files and commit messages are verified against the source and marked documented; everything else is marked inferred.
- Diagrams drawn from facts at view time (imports, dependencies, infrastructure and the whole system), with the file and line behind every node and arrow.
- Validation before anything is saved: quotes, fact links, diagram placeholders, checks, and a secret scan of every page and answer.

**Updates**
- `codetrail update`: refreshes the sources and facts for free, shows the estimate, and asks before any paid work. `--facts-only` calls no assistant; `--yes` goes ahead without asking.
- Incremental: pages your new commits touched are written first, then pages catching up; a page with few changes is revised section by section instead of rewritten; a page that failed is skipped until its facts change (`--retry-failed` tries it anyway). The estimate lists each page and why.
- Hard limits: a dollar and a token budget per update, a page cap, and all-or-nothing commits to the guide.

**The page**
- `codetrail serve`: the guide as a local web page with progress first, search (⌘K or /), a command palette, keyboard shortcuts, and pages for progress, saved answers, digests and decisions.
- Ask from any page: questions go to your assistant with read-only tools, answers stream into the page, and the ones worth keeping are saved to the guide.
- The update panel: start an update from the page and watch each step, with the provider, model, tokens and cost of every page.
- Learning: checks graded against rubrics grounded in the code, progress per path, and staleness that shows what changed in a page you learned.
- The interface is translatable with gettext catalogs, including right-to-left languages; it ships in English.

**Assistants and costs**
- Providers behind one interface: Claude Code on a Claude subscription (the default), Codex on a ChatGPT subscription (off unless a target opts in), and local models through Ollama or LM Studio.
- `codetrail providers`: whether each provider is installed and signed in, and how.
- An estimate before every paid action, from your own call history once there is one, with your plan's usage windows; each call's actual tokens and cost are recorded.

**Security**
- The page and the bridge listen on 127.0.0.1 only, check `Host`, `Origin` and `Sec-Fetch-Site`, need a single-use login code and a per-session token, and send a strict content security policy.
- The assistant gets read-only tools (Read, Grep, Glob) confined to the filtered sources: for Claude Code through two independent guards, and for local models through Codetrail's own tools. Codex can't be confined the same way, so it stays off unless a target opts in. Every provider gets an allowlisted environment: Codetrail never reads, stores or passes a key or token, unless you set `auth = "api_key"`, when the provider's key variable is handed to its program by name.

[Unreleased]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.0.0...develop
[1.0.0]: https://github.com/mohamed-abdelsattar92/codetrail/releases/tag/v1.0.0
