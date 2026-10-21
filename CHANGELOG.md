# Changelog

Every notable change to Codetrail, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Context for coding agents. Each update now writes a short map of the repository (its parts and how they connect, its decisions, recent changes and the guide's pages) and a brief per area and concept page (key files, conventions and the reasons for them, still marked documented or inferred) to Codetrail's data folder. `codetrail mcp <target>` serves them to Claude Code and Codex as read-only tools, so an agent starting a task reads Codetrail's understanding instead of rediscovering the repository; `codetrail agents <target>` prints how to connect them. Briefs come from the call that already writes each page, so they add only a little output to each estimate, and nothing an agent asks for costs a call ([ADR 0017](docs/adr/0017-tool-server-for-coding-agents.md)).

### Changed
- Every diagram (the system diagram, and the imports, dependencies and resources diagrams on area pages, guide pages and answers) is drawn with D2 instead of Mermaid, with a clearer layout and each kind of part in its own shape as before. The first diagram on a page takes about a second to appear while the browser prepares D2. The README's diagrams are drawn with D2 too ([ADR 0016](docs/adr/0016-draw-diagrams-with-d2.md)).

### Security
- The page's Content-Security-Policy no longer allows inline styles, which only Mermaid needed. D2's layout evaluates JavaScript, so it runs in a worker with its own policy, which can load nothing but its own two files and has no access to the page, and its drawing is shown as an image, which can't run script or follow links.

## [1.3.0] - 2026-10-18

Release notes: [docs/releases/v1.3.0.md](docs/releases/v1.3.0.md).

### Changed
- The home page is redesigned to use its space and show more in color: learning sits in a wide column (where to continue, with a ring for the path's progress; every guided path with its own progress; the areas, each edged and labelled learned, changed, read or not read yet) beside a rail with the guide's state against the branch, what changed, the System, Documentation and Repository cards, each in its own color, your saved answers and the folders to browse as chips. The rail moves below the column when the page is narrow, including when the Ask panel takes the room.

### Fixed
- The Repository timeline's "Watched by Codetrail since" label shows in full when the band is too narrow for it, just before the band's edge, instead of being cut to nothing; and the arrows on the Documentation page's section headings sit centred on the heading's text.
- A learned page's green tick in the sidebar shows on its own again, instead of inside a large bordered card meant for the checks on a guide page.
- The Documentation page no longer takes a minute or more to open the first time after an update on a repository with images under `docs/`: images and other binary files that match the document globs are skipped instead of being read as text, which also stops their bytes from counting as a document mentioning a package.
- A folder or file whose name holds `#` or `?` (such as `docs#1/` or `0002-use-c#.md`) now opens from its link on the home page, the area, fact, decisions and system pages and the Repository page, instead of opening the wrong folder or a page that isn't found.
- The same names now open from every other link to them too: the boxes of the imports, dependencies, resources and system diagrams, a guide page's fact links and cited lines, search results, and the links between facts (a module, project or package whose name holds `#` or `?`).
- A fact whose id reads as a path, such as an npm dependency named `../../../system` in a `package.json`, now opens from its links instead of the browser resolving them to another page (here, System). Fact links now encode the `/` in a fact id as `%2F`; links with a raw `/` still open.

### Security
- A repository whose `Package.swift` files list many targets and many dependencies or imports, or whose deeply nested Swift files hold many imports, can no longer stall an update: Swift targets are found by name and by folder in one lookup each, instead of a scan of every target, and each file's target is found once.
- The command palette offers a search result only when its link stays on Codetrail's own page, judged the way the browser reads the link, so a link such as `/\example.com` (which browsers treat as `//example.com`) can never take you to another site. Codetrail's own results never held such links; this guards against one ever appearing.

## [1.2.0] - 2026-10-11

Release notes: [docs/releases/v1.2.0.md](docs/releases/v1.2.0.md).

### Added
- A Documentation page (in the sidebar, or G then O) and a home card show how well the repository explains itself: how much of the guide's rationale is documented and from where (ADRs, documents, code comments, commits), which commits explain why, which ADRs need attention, and which dependencies, projects and resources no document mentions. Each number lists what's behind it, and each update keeps a trend. `codetrail metrics <target>` prints the same numbers. None of it calls your assistant.
- The Documentation page shows an overview beside folding sections, each with a chart for its numbers: the section most in need opens first, an explanation of what each section counts folds away, and the long lists of commits and unmentioned facts filter by kind.
- A Repository page (in the sidebar, or G then T) and a home card show what the repository is: when it started, its commits and activity month by month, its releases from the tags on the branch, its lines of code by language with the share in tests, and its facts by kind with the dependencies that arrived and left. Commits, lines, files and the test share keep a trend across updates. `codetrail stats <target>` prints the summary. None of it calls your assistant, and no author is read or shown.
- The Repository page is drawn as charts you can explore: an interactive timeline of commits by day, week or month with the releases and dependency changes on it, a day-by-day activity calendar, a heat map of where change happens by guide area, folder or file with a side panel, a grid of each area's commits by month, and charts for commit size, languages, commit types and facts. The numbers stay in tables under each chart, and the charts follow the interface language and theme.
- The Repository page shows where change happens: the most changed files and folders over the latest commits, each guide area's commits beside how much of its rationale is documented (much change and mostly inferred is where an ADR or a README would help most), code no commit has changed in a year, and how many files a commit changes.
- The sidebar can be hidden (the button at the start of the header, or \\) so the page takes the whole width, and peeks out over the page when you rest the pointer on the window's edge. Its sections fold, and pages you pin sit as tiles at its top, kept by Codetrail so they follow you to any browser. The page now sits in a card inside a tinted frame.

### Changed
- Codetrail's mirror of a repository now keeps the tags that point into its branch, for the releases; tags elsewhere, and their commits, still never come in.

### Fixed
- The first update of a new target, started from the page, no longer fails now and then with an error in the update panel ("table … already exists"), or leaves the database locked, when the page reads the database while the update creates it.

### Security
- A revision read from Codetrail's data folder can no longer be taken by git as an option, and a `source.json` whose commit isn't a full commit name is refused, with a message to run the update again.

## [1.1.1] - 2026-10-09

Release notes: [docs/releases/v1.1.1.md](docs/releases/v1.1.1.md).

### Added
- An Arabic interface: choose العربية in the language picker. It reads right to left, keeps technical terms such as commit and branch in English, and writes numbers with Western digits.
- Arabic text in the page (answers, feedback and any Arabic interface text) is set in Noto Sans Arabic, bundled with Codetrail, instead of whatever font the system has.

### Fixed
- The page's messages (refusals, the update panel's reasons, a check's verdict) now come in the interface language instead of always in English.
- In a right-to-left language, interface headings and labels no longer space their letters apart, and the Ask and update panels slide in from their own side.
- Search results name their kind (concept, path…) in the interface language, and keep the guide's English left to right; estimates on buttons no longer lose their leading tilde in right to left.

## [1.1.0] - 2026-10-08

Release notes: [docs/releases/v1.1.0.md](docs/releases/v1.1.0.md).

### Added
- A Sponsor button on the GitHub repository, linking to Ko-fi.

### Changed
- Codetrail is now licensed under the [Apache License 2.0](LICENSE) instead of the GNU AGPL 3.0 only, so you may use it commercially without asking. Versions 1.0.0 and 1.0.1 keep the AGPL.

### Removed
- The contributor licence agreement: contributions are licensed under the Apache License 2.0, as its section 5 says, with nothing to sign.
- The commercial licence offer, which the Apache License makes unnecessary.

## [1.0.1] - 2026-10-07

Release notes: [docs/releases/v1.0.1.md](docs/releases/v1.0.1.md).

### Added
- A security policy, [SECURITY.md](SECURITY.md): how to report a vulnerability privately, and what counts as one.
- Contribution rules in [CONTRIBUTING.md](CONTRIBUTING.md): git-flow, every change through a pull request, simple code, tests first, the changelog, and how a release reaches `main` through a pull request.

### Changed
- Codex's consent is also checked when the assistant is built, before Codetrail looks for any provider's program. Loading a target already refused Codex without `allow_codex`, so nothing changes for users; this is a second layer.

### Fixed
- Getting started's troubleshooting table quotes the message you actually see when a target uses Codex without allowing it.
- CI passes on GitHub's runners: two tests depended on the machine they ran on (whether Claude Code was installed, and how long since it booted).

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

[Unreleased]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.3.0...develop
[1.3.0]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/mohamed-abdelsattar92/codetrail/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/mohamed-abdelsattar92/codetrail/releases/tag/v1.0.0
