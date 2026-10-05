# Security review checklist

Codetrail runs Claude over repositories that hold secrets and serves a local page that can drive Claude, so it is security-first: security wins ties with convenience. Before a feature branch is finished, its commits get the review below. In Claude Code the `security-reviewer` agent (`.claude/agents/security-reviewer.md`) runs it; any other agent or person follows this file directly. It follows the founder's earlier checklists; the process, standards and report are the same, and the product sections are Codetrail's.

## When it runs
1. The author finishes the branch's work, runs its tests and commits it.
2. The review runs on the branch's commits, never on the working tree, so the author can start the next branch while it runs. The author gives the reviewer only the branch's name and the results of its tests.
3. When the review returns, the author fixes what blocks with new commits on the branch, and the fixes are reviewed again.
4. The branch is finished only on *pass* or *pass with notes*, and only if its last commit is the one reviewed. The verdict and that commit go in the merge commit's message (AGENTS.md, Workflow).

The author launches this review, so it is a check, not an independent gate. The independent control is the founder, who reads each merge before pushing it.

## Standards we hold to
Check the current version of each before citing it; when a newer one exists, use it and update this list.
- [OWASP Top 10:2025](https://owasp.org/Top10/2025/), for the local page and bridge.
- [OWASP ASVS 5.0](https://github.com/OWASP/ASVS), for concrete requirements.
- [OWASP Top 10 for LLM Applications 2026](https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/), for generation, answers and grading.
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/), for Claude running with tools inside Codetrail, and for the coding agents that build it.
- [CWE Top 25 (2025)](https://cwe.mitre.org/top25/).
- The [OWASP Secure Headers Project](https://owasp.org/www-project-secure-headers/), for HTTP response headers.

## How to review
1. Pin what you review. First check that `git for-each-ref refs/replace` prints nothing, since a replace ref could swap a file's content, and that `git for-each-ref '--format=%(symref)' refs/remotes/origin/develop` prints nothing, since a symbolic ref could point the founder's pushed copy at the branch under review. When the founder gave the commit they last pushed, check that `git rev-parse refs/remotes/origin/develop` equals it. Read these rules from what the founder has pushed, by the ref's full name and with replace objects off: `git --no-replace-objects show refs/remotes/origin/develop:<path>`, or from `develop` only while `origin/develop` has no copy yet, or from the branch only while neither has one. Then run `git rev-parse <branch> develop` once, where `<branch>` is the branch under review. Use those two commits, `<sha>` and `<develop-sha>`, for everything below, so neither can move under you. Read the change through git, never from the working tree, which may hold another branch by then:
   - `git log <develop-sha>..<sha>` lists its commits;
   - `git diff <develop-sha>...<sha>` shows everything it changes;
   - `git show <sha>:<path>` shows a file as the branch has it.

   Then read the files it touches and anything that calls them. A change is only safe in its context. Review the whole diff even when told to look at part of it, and list anything you were asked to skip under "Not checked".
2. Work out what the change exposes. List the new entry points (commands, routes, background jobs, CI triggers), the data they take in, where that data goes, and who can reach them. Walk through each section below that applies and skip the rest.
3. Run the checks that apply, using only the tools pinned in the repository, and only when `mise which <tool>` finds them; otherwise list the check under "Not checked". Run gitleaks by the path `mise which gitleaks` prints, so no mise setting reaches it, and the audit through `mise exec --` as written. Never install anything.
   - Start every Bash call with `export MISE_EXEC_AUTO_INSTALL=false;`. Before running a tool, stop and report if:
     - a mise file in the working tree sets environment variables in any spelling (an `[env]` table, `env.NAME =` keys or an inline `env = {…}`), which would reach the tools the audit runs: for each of `.mise.toml`, `mise.toml`, `.mise.local.toml`, `mise.local.toml`, `.config/mise.toml`, `.config/mise/config.toml`, `.mise/config.toml` and `mise/config.toml` that `test -e` finds, and their `MISE_ENV` variants such as `mise.dev.toml`, `grep -c -E '^[[:space:]]*(\[env|env[[:space:]]*[.=])' <file>` must print 0; and if `test -d` finds a `conf.d` folder in `.mise`, `.config/mise` or `mise`, stop and report, since its files load too;
     - `mise which <tool>` isn't the whole path of mise's own install of the version pinned in `.mise.toml`, for example `/Users/<you>/.local/share/mise/installs/gitleaks/8.30.1/gitleaks`, since a `path:` entry could swap in any program under a look-alike folder;
     - `.gitleaks.toml` or `.gitleaksignore` exists in the git folder (`git rev-parse --absolute-git-dir`, then `test -e <that folder>/.gitleaks.toml` and `test -e <that folder>/.gitleaksignore`), since gitleaks would read them.
   - Secrets in the branch's commits, with gitleaks' configuration variables cleared, where `<gitleaks>` is the whole path `mise which gitleaks` printed: `GITLEAKS_CONFIG= GITLEAKS_CONFIG_TOML= <gitleaks> git "$(git rev-parse --absolute-git-dir)" --redact --no-banner --ignore-gitleaks-allow --gitleaks-ignore-path /dev/null --log-opts="<develop-sha>..<sha>"`. Scanning the `.git` folder means no `.gitleaks.toml` or `.gitleaksignore` from the working tree can quietly allow a secret, and `gitleaks:allow` comments are ignored.
   - When dependencies change, check them for known vulnerabilities. Audit copies of the branch's lockfiles alone in temporary folders, so no setting the branch adds can steer the audit, and with automatic tool installs off. Run it in one Bash call, exactly as written with the branch's 40-character commit id in place of each `<sha>`: the reviewer's hook allows this command and no variation of it. This covers the commit-message tooling's JavaScript packages and Codetrail's Python lockfile:
     ```
     export MISE_EXEC_AUTO_INSTALL=false; js=$(mktemp -d) py=$(mktemp -d) && git show <sha>:pnpm-lock.yaml > "$js/pnpm-lock.yaml" && git show <sha>:pyproject.toml > "$py/pyproject.toml" && git show <sha>:uv.lock > "$py/uv.lock" && mise exec -- pnpm --dir "$js" audit --registry=https://registry.npmjs.org/; mise exec -- uv audit --frozen --no-build --no-config --directory "$py"; rm -rf "$js" "$py"
     ```
   - An audit ignore list, audit level, proxy, registry or TLS setting that the branch adds is a finding in itself.
   - Don't run tests or start services; the author reports the test results.
4. Report as described at the end. Stay read-only: never edit, commit, deploy or change settings. In Claude Code, the reviewer's hook (`.claude/hooks/reviewer_allowlist.py`) enforces this: it allows only read-only git with the options it lists, the gitleaks and audit commands above exactly, `mise which`, `test -e`, filters on a pipe with no files, and pages on standards sites. When it blocks a command you need, put that check under "Not checked".

## What to check

### Target repositories stay read-only
- Nothing writes to a target repository: no `git` command that changes its index, refs, config or hooks (`status`, `fetch` run inside it, `worktree`, `gc`), no file written under its path, no hardlink to its objects (the mirror clones with `--no-local`).
- The mirror and `source/` live only in Codetrail's data folder; a configured path inside the target is refused.

### Secrets and exclusions (design section 3)
- No secret, key, token or password in the change, including tests, fixtures, docs and examples. Test secrets are assembled at runtime.
- Every path that reads target content goes through `repo`'s exclusion rules: the built-in secret patterns (which nothing overrides), the ignore files, and gitleaks' flagged files. That covers extractors, Claude's tools, source views, logs, diffs, digests and the "you're behind" counts.
- Symlinks and submodules are never materialized; paths are resolved before they are checked, so `..`, absolute paths and links can't leave `source/`.
- Missing ignore rules or a missing or failing gitleaks fail closed.
- Secrets and file contents never appear in logs, error messages or the update summary (paths and rule names only).

### Claude as an agent (LLM and Agentic Top 10)
- Claude gets only Read, Grep and Glob through the tool guard, rooted at `source/`, and no tools at all for grading. No Bash, Edit, Write, web or MCP tools; no settings, hooks or `CLAUDE.md` loaded from the target.
- Repository content and the reader's answers are untrusted input to Claude. Prompts separate instructions from that content. Claude's output never executes, never becomes HTML without the renderer's escaping, and never decides access.
- Documented rationale is verified against its source before a page is saved.
- Model calls are bounded: the update budget, `max_turns`, one question in flight, question length.

### The local page and bridge (design section 7.4)
- The server binds `127.0.0.1` only; another host in configuration is refused.
- Every request checks `Host`; every route but `/login` needs the session; every `POST` checks `Origin` and the `X-Codetrail-Token` header. The login code is single-use and expires.
- Session tokens and codes come from `secrets`, carry at least 128 bits, and are compared in constant time.
- Input is checked at the boundary against an allowlist: language codes against installed catalogs, page and fact ids against the guide and the store, lengths limited.
- Responses carry `Content-Security-Policy` (as in the design, with `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`.
- Rendering: Jinja2 autoescaping on, Markdown with raw HTML off, only `http(s)` and relative links, Mermaid labels escaped and `securityLevel: "strict"`, no inline scripts.

### Input and injection (A05)
- SQL is parameterised only; identifiers come from fixed code.
- Nothing from outside reaches a shell, `eval`, a template compiler, a file path or a regular expression built at runtime. Subprocesses take argument lists, never `shell=True`.

### Errors and logging (A09, A10)
- The page shows generic errors, never stack traces, SQL or file contents.
- The code fails closed. An error in a check (the exclusion rules, the tool guard, a token check, a verdict's parsing) means "no".

### Agent guardrails (Agentic Top 10: ASI01 to ASI06)
The rules that keep coding agents safe live in the repository, so a branch could weaken them.
- The report names every change to these files, so the founder sees it:
  - the agent rules: any `AGENTS.md`, `CLAUDE.md` or `CLAUDE.local.md` at any depth, `.claude/`, `.codex/`, `.mcp.json` and `docs/security/`;
  - the hooks and their tests: `tools/git-hooks/`, `tools/hooks/`, `tools/tests/`, any `lefthook*` file and the `commitlint*.mjs` files;
  - the git-flow settings, `.gitflow`;
  - CI: all of `.github/`;
  - the tools and package settings: any mise file, `justfile`, `package.json`, `.npmrc`, `uv.toml` and the `[tool.uv]` sections;
  - `.gitignore`, `.gitattributes`, `.gitleaks.toml` and `.gitleaksignore`.
- A change that removes or relaxes a Never-do rule, a permission rule, a hook check or an item of this checklist is **high**, unless the founder asked for it in the task.
- Agents get the fewest tools and permissions their job needs.

### Supply chain and CI (A03, A08)
- A new dependency has an ADR (AGENTS.md, rule 7). It is maintained and widely used, is pinned in a lockfile, and has no known vulnerability in the pinned version. Vendored files record their version and licence.
- GitHub Actions:
  - Every action, GitHub's own included, is pinned to a full commit SHA.
  - `actions/checkout` sets `persist-credentials: false` unless a later step needs the token.
  - Each workflow and job sets the smallest `permissions:`.
  - `pull_request_target` never checks out code from the pull request.
  - No `${{ github.event.* }}` or other untrusted value is interpolated into `run:`.

## Severity
- **Critical:** exploitable now, leading to data exposure, code execution, a write to a target repository or a leaked secret. It blocks finishing the branch.
- **High:** exploitable with modest effort, or it removes a required control (for example, a missing token check or an exclusion path that is skipped). It blocks finishing the branch.
- **Medium:** it weakens defence in depth or would become exploitable after a likely future change. Fix it on the branch, or record why not in the merge message.
- **Low:** hardening or hygiene. Fix it when cheap; otherwise list it as a follow-up.

## The report
- **Verdict:**
  - *pass*: no findings;
  - *pass with notes*: medium or low findings only;
  - *blocked*: any critical or high finding.
- **Findings, most severe first.** For each one:
  - its severity and a one-line title;
  - the location as `path:line`;
  - the concrete attack or failure, with its inputs and result;
  - the fix;
  - the standard it breaks, for example "OWASP A01:2025" or "ASVS v5.0.0-2.2.1".
- **What was reviewed,** as the report's first line: `Reviewed <branch> at <sha> against develop <develop-sha>`.
- **Checks run,** with their results.
- **Not checked:** what the reviewer could not verify, and why.

The author finishes only if `git rev-parse <branch>` still equals `<sha>`; otherwise the new commits are reviewed first. The merge message carries the verdict, the reviewed commit and each finding's title, for example `Security review: pass with notes (reviewed 4a19317): three low findings, listed below`. A medium finding left unfixed is recorded, with the reason, in the merge message and in the final report to the founder.
