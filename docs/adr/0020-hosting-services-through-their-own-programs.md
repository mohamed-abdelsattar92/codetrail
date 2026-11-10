# 0020. Reach GitHub, Azure DevOps and GitLab through the reader's own command-line programs, and never hold a token

- Status: accepted (by the founder, 10 November 2026)
- Date: 2026-11-10
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), in the design session of 2026-11-10 on teams across hosting services
- Design: `docs/design/2026-10-05-codetrail-design.md` section 24 (24.3, 24.4 and 24.11); brainstorm decision 16; AGENTS.md non-negotiables 1 and 4

## Context
Section 24 lets a tech lead follow a team's repositories where they are hosted, on GitHub, Azure DevOps or GitLab, and catch up on all of them from one page. That needs two things Codetrail doesn't do today: listing an organization's (or project's, or group's) repositories, and fetching each one over the network instead of from the reader's checkout.

Both cross rules Codetrail holds:
- AGENTS.md, non-negotiable 4: "The only services Codetrail calls are the assistant providers the reader configures." A hosting service is a new external service, which needs an ADR (AGENTS.md, "Never do", rule 7).
- AGENTS.md, non-negotiable 1, and section 15.2: "Codetrail never reads, stores or passes a key or token." Every service's API needs one for private repositories.

Facts:
- Each service has an official command-line program that keeps its own sign-in: `gh` (GitHub CLI), `az` with the `azure-devops` extension (Azure CLI), `glab` (GitLab CLI). Each has a status command (`gh auth status`, `az account show`, `glab auth status`) and commands that list repositories as JSON.
- Each can also lend git its sign-in when the reader asks it to (`gh auth setup-git`; `glab auth login` offers it). Azure DevOps works with Git Credential Manager or an SSH key.
- Codetrail already runs the reader's own programs for its assistants (`claude`, `codex`) with an allowlisted environment and no key (ADR 0006, section 15.2). The pattern, its process limits and its tests exist.
- The exact listing commands and their JSON fields haven't been checked against the installed programs yet. A spike opens Phase 21 to settle them.

## Decision drivers
- Codetrail never holds a token (non-negotiable 1).
- Codetrail never writes to a service, as it never writes to a target (rule 6, in spirit).
- Local by default: no hosted service of Codetrail's own, no callback URL, nothing listening beyond `127.0.0.1` (non-negotiable 4).
- The best experience for a tech lead adding a whole organization, which needs a list of its repositories, not URLs typed one by one.
- Plug-and-play: one interface, one adapter per service, a fake for tests (non-negotiable 5).
- Few dependencies: nothing new in `pyproject.toml`.

## Options considered
### Option A: the reader's own command-line programs (`gh`, `az`, `glab`)
- Good, because the sign-in stays in the program, in the reader's keychain or configuration; Codetrail runs the program with an allowlisted environment that drops token variables, and never sees a token.
- Good, because it follows a pattern Codetrail already has and tests (ADR 0006).
- Good, because it adds no Python dependency, and listing an organization gives the add-repositories screen what it needs.
- Bad, because the reader installs and signs in to one more program per service, and Azure DevOps needs an extension.
- Bad, because each program's commands and output can change between versions. Mitigated by the adapters validating every field they read, and a live test with the installed programs.

### Option B: Codetrail calls the services' REST APIs with a personal access token
- Good, because there's no program to install, and the APIs are stable and documented.
- Bad, because Codetrail would read, store or pass a token: in its configuration, in the Keychain, or from an environment variable. That breaks non-negotiable 1, and a leaked token reaches far beyond Codetrail.

### Option C: an OAuth app (or GitHub App) that the reader authorizes
- Good, because the reader clicks "Authorize" instead of creating a token.
- Bad, because Codetrail would hold the resulting token, and the flow needs a registered app and a callback that Codetrail controls. That's a hosted piece, against non-negotiable 4.

### Option D: git only, with the reader pasting each repository's URL
- Good, because it needs no service API at all, only the reader's git sign-in.
- Bad, because there's no list of an organization's repositories, so "follow the whole organization" and the checklist can't exist, and new repositories are never noticed. It fails the experience the design is for.

## Decision
Option A. Codetrail lists sources, repositories and branches through the reader's own `gh`, `az` and `glab`, behind a `hosting` interface with one adapter per service and a fake. It runs them as it runs the assistant programs: absolute paths, an allowlisted environment without token variables, its own process group and a time limit. It runs only commands from a fixed read-only list. git fetches over `https` or `ssh` only, with the reader's own git sign-in, and never prompts. Codetrail sets no credential helper and writes no git configuration of the reader's. When git can't fetch, Codetrail names the command the reader can run.

It wins on the first three drivers, where B and C fail, and on experience, where D fails.

## Consequences
- Easier: a tech lead follows a whole organization in one screen, and Codetrail stays free of tokens.
- Harder: three more programs to support, each with its own version changes; network fetches in the free signal, which can be slow or fail per repository; the page serving many targets under one server (section 24.7).
- Non-negotiable 4 widens: Codetrail also calls the hosting services the reader configures, only through the reader's own programs and git, only to read.
- Revisit if a service's program stops lending git its sign-in, or stops listing repositories as JSON; or if readers need a host the programs don't support.

## Changes required
- [x] AGENTS.md, non-negotiable 4: name the hosting services, reached only through the reader's own programs and git, read only.
- [x] `docs/design/brainstorm-decisions.md`: decision 16 approved.
- [x] `docs/design/2026-10-05-codetrail-design.md`: section 24 approved; section 1's "Not goals" unchanged (still no hosted or multi-user service).
- [x] `docs/security/review-checklist.md`: the hosting programs' environment and read-only commands, remote transports, the team routes (Phase 21).
- [x] `docs/adr/README.md`: this ADR's status in the index.
