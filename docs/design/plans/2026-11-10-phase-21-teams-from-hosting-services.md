# Phase 21: teams from hosting services implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reader follows a team's repositories straight from GitHub, Azure DevOps or GitLab. They choose them on one screen ("follow the whole source" or a hand-picked list, each on its chosen branch), and `codetrail team serve` shows every repository with its free "you're behind" signal and its full guide, all under one login.

**Architecture:**
- **`codetrail.hosting`** has one adapter per service (`github`, `azure_devops`, `gitlab`) and a fake. Each adapter runs the reader's own `gh`, `az` or `glab` through a read-only runner with an allowlisted environment, so Codetrail never sees a token.
- **Remote targets:** a target gains `remote` in place of `repository`. Its mirror is fetched over `https` or `ssh` with the reader's own git sign-in. Everything after the mirror (exclusions, scans, facts, the guide) is unchanged.
- **`codetrail.team`** keeps teams' settings, decides which repositories a team follows, and writes their targets.
- **The team server** mounts each target's existing app under `/repos/<target>/` behind one security middleware. To make that possible, every link the page builds goes through `site_path()`.

**Tech Stack:** Python 3.14, FastAPI 0.142 / Starlette 1.7, pydantic, Jinja2, plain ES modules, pytest, Playwright. No new dependency (ADR 0020).

**Spec:** [design section 24](../2026-10-05-codetrail-design.md#24-teams-catching-up-on-every-repository-a-team-works-in) (approved 10 November 2026), [ADR 0020](../../adr/0020-hosting-services-through-their-own-programs.md) (accepted), brainstorm decision 16. Phase 22 (the catch-up, highlights, the overview and the team page's cards) is out of scope.

## Global Constraints

- **Never write to a service.** Every hosting command is checked against its adapter's read-only list before it runs (design 24.3). Nothing in this phase runs `gh auth setup-git`, `glab auth login` or any command that changes the reader's git or program configuration, the spike included (AGENTS.md, Never do, rule 2).
- **No token is read, stored or passed.**
  - The hosting programs get exactly: `PATH` (relative entries dropped), `HOME`, `USER`, `LOGNAME`, `LANG`, `LC_*`, `TMPDIR`, `TERM`, `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` and `NO_PROXY` (in either case), `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, and `GH_CONFIG_DIR`, `AZURE_CONFIG_DIR` and `GLAB_CONFIG_DIR` when set.
  - git for a remote target gets the same, plus `SSH_AUTH_SOCK` and `GIT_SSL_CAINFO`, and `GIT_TERMINAL_PROMPT=0`, `GIT_CEILING_DIRECTORIES=/` and `GIT_SSH_COMMAND=ssh -o BatchMode=yes`.
  - Token variables never pass: `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `AZURE_DEVOPS_EXT_PAT`, `SYSTEM_ACCESSTOKEN`, `GITLAB_TOKEN`, `GITLAB_ACCESS_TOKEN`, `CI_JOB_TOKEN`, and any the spike adds.
  - A local target's git environment is unchanged.
- **Remote transports:** git runs with `-c protocol.allow=never -c protocol.https.allow=always -c protocol.ssh.allow=always`. A clone URL's host must be the service's:
  - `github.com` (https) or `git@github.com` (ssh);
  - `dev.azure.com` (https) or `ssh.dev.azure.com` (ssh);
  - `gitlab.com` (https) or `git@gitlab.com` (ssh).

  No team or target setting can widen this. Only code can, and only tests do.
- **Global settings** `[hosting]`: `gh = "gh"`, `az = "az"`, `glab = "glab"`, `timeout_seconds = 60` (5–600), `max_output_bytes = 10_000_000` (100_000–100_000_000), `max_repositories_per_source = 1000` (1–10000), `concurrency = 4` (1–16). `[team]`: `active_days = 90` (1–3650).
- **Names:**
  - A team is `[a-z0-9][a-z0-9-]{0,29}`.
  - A target created by a team is `<team>-<repository>`: lower-cased, every run of characters outside `[a-z0-9-]` replaced by one `-`, trimmed of `-`, and cut so it matches `TARGET_NAME` (at most 63 characters). On a clash it ends with `-` and the first 6 lower-case alphanumerics of the service's id.
  - `teams` is reserved: no target may be named `teams`, since a team's data lives in `<data>/teams/<team>/`.
- **Following a whole source** follows repositories pushed to within `team.active_days`. It never follows archived, disabled or empty repositories (no default branch). Forks and templates count. A followed repository that goes quiet stays followed (design 24.5).
- **The page never deletes data.** `codetrail target remove` refuses on a target its team still follows (design 24.4).
- **The team server is the existing page, mounted.** One `SecurityMiddleware` wraps the team app. The token, `Origin`, session and `Sec-Fetch-Site` rules apply to `/repos/<target>/…` exactly as to the same path at `/`. `/static/` stays at the root, shared.
- **Commits:** Conventional Commits with a scope (`config`, `repo`, `web`, `tools`, `docs`; this phase may use `hosting` and `team` too, so Task 2 adds both to `commitlint.config.mjs` and AGENTS.md). Every commit body has What, Why, Alternatives considered, Risks and Agent and model, with lines of at most 100 characters. Run commands through `mise exec --`.
- **Neutral examples only:** `acme-org`, `contoso/Platform`, `example-group`, `web-shop`, `payments-api`. Never the founder's own organizations or repositories, not even from the spike.

## Review Focus

1. **A token variable set in the reader's shell** (`GH_TOKEN` exported for another tool): the program still uses its saved sign-in, and the variable never reaches the program or git. Test in Task 3 (`test_token_variables_never_reach_the_program`) and Task 5 (`test_remote_git_gets_no_token_variables`).
2. **A token-gated read under a mount** (`/repos/web-shop/bridge/answers` without the token header): refused exactly as `/bridge/answers` is. Test in Task 7 (`test_token_reads_under_a_prefix_need_the_token`).
3. **A repository or project name with spaces, `#`, `..` or non-ASCII** (Azure DevOps allows many of them): it is validated before use, passed as its own argument, shown escaped, and given a target name that matches `TARGET_NAME`. Test in Task 2 (`test_target_names_from_awkward_repository_names`) and Task 3 (`test_azure_names_with_spaces_are_one_argument`).
4. **A posted selection naming a repository id the service didn't list, or a branch like `--upload-pack=x`:** refused, with nothing written. Test in Task 9 (`test_unknown_ids_and_bad_branches_are_refused`).
5. **A remote that can't be reached or needs a sign-in git doesn't have:** the refresh fails within the time limit without prompting, and names what the reader can run themselves. The team page shows that repository as unreachable while the others load. Test in Task 5 (`test_a_fetch_that_needs_a_sign_in_names_the_fix`) and Task 8 (`test_one_unreachable_repository_does_not_block_the_page`).

---

### Task 1: the spike

Settle what design 24.3 leaves to the spike, against the installed `gh` (2.101.0 here) and the `az` and `glab` documentation (neither is installed here). This task produces facts and fixtures, not product code.

**Files:**
- Modify: `docs/design/2026-10-05-codetrail-design.md` (section 24.3: a table of the exact commands and fields per service, dated)
- Create: `tests/fixtures/hosting/github_repos.json`, `github_orgs.json`, `github_branches.json`, `azure_projects.json`, `azure_repos.json`, `azure_branches.json`, `gitlab_groups.json`, `gitlab_projects.json`, `gitlab_branches.json` (hand-written in each command's output shape, with the neutral names from Global Constraints)

- [ ] **Step 1: GitHub, live, read-only.** Run `gh auth status`, `gh api --method GET user/orgs`, `gh repo list <owner> --json id,name,defaultBranchRef,isArchived,isEmpty,isFork,isTemplate,pushedAt,diskUsage,url,sshUrl --limit 5`, and `gh api --method GET repos/<owner>/<repo>/branches --paginate`. Record each command's exit codes (signed in or not) and output fields. Also note whether a disabled flag exists and how the account name appears in `gh auth status --json` (if it has one). Don't save real output in the repository.
- [ ] **Step 2: git's sign-in, without changing anything.** Run `git config --get-all credential.https://github.com.helper` and `git ls-remote https://github.com/git/git HEAD` (a public repository: no sign-in needed), and record whether a helper is set. Don't run `gh auth setup-git`: it changes the credential helper. The fix text names it for the reader to run.
- [ ] **Step 3: Azure DevOps and GitLab, from their documentation.** Settle: `az account show` and `az devops project list --organization https://dev.azure.com/<org> -o json`; `az repos list --organization … --project … -o json` (fields `id`, `name`, `defaultBranch`, `isDisabled`, `size`, `remoteUrl`, `sshUrl`, `webUrl`, and how archived or empty shows); `az repos ref list … --filter heads/ -o json`; `glab auth status`; `glab api --method GET groups?min_access_level=10`; `glab api --method GET groups/<id>/projects?include_subgroups=true&archived=false`; `glab api --method GET projects/<id>/repository/branches`. Add the token variables these documents name to the list in Global Constraints. Mark both adapters "from documentation, not yet run" in section 24.3.
- [ ] **Step 4: Write the fixtures** in each command's shape: two repositories per service, one archived, one with a name that needs care (Azure: `Payments API #2`).
- [ ] **Step 5: Commit** (`docs(docs): settle the hosting programs' commands in the Phase 21 spike`).

### Task 2: settings for remote targets, teams and hosting

**Files:**
- Modify: `src/codetrail/config.py`, `commitlint.config.mjs` and AGENTS.md's list of scopes (adding `hosting` and `team`)
- Test: `tests/unit/test_config.py`

**Interfaces — produces:**
- `Service = Literal["github", "azure_devops", "gitlab"]` (in `codetrail.hosting.names`, Task 3; `config` imports it).
- `class HostingSettings(Settings)` and `class TeamSettings(Settings)` with the values in Global Constraints; `GlobalConfig.hosting`, `GlobalConfig.team`.
- `class RemoteSettings(Settings): service: Service; source: str; id: str; name: str; url: str`. A validator calls `check_clone_url(service, url)` (Task 3).
- `TargetConfig.repository: Path | None = None`, `TargetConfig.remote: RemoteSettings | None = None`, `TargetConfig.team: str | None = None`; a validator requires exactly one of `repository` and `remote`; the property `location -> str` is the path or the URL.
- `class TeamSource(Settings): service: Service; source: str; follow: Literal["all", "chosen"] = "all"; clone_over: Literal["https", "ssh"] = "https"; left_out: list[str] = []; chosen: list[str] = []`, where `source` passes `validate_source`.
- `class TeamConfig(Settings): sources: list[TeamSource] = []`.
- `TEAM_NAME`, `validate_team_name(name) -> str`, `Paths.team_file(name) -> Path` (`<config>/teams/<name>.toml`), `Paths.team_data(name) -> Path` (`<data>/teams/<name>`), `load_team(paths, name) -> TeamConfig`.
- `validate_target_name` refuses `teams`.
- `write_remote_target(paths, name, remote: RemoteSettings, branch: str, team: str) -> Path`: like `write_target`, `open("x")`, values written with `json.dumps`, and no `check_containment` (there is no checkout).

- [ ] **Step 1: Write the failing tests.**
  - `test_a_target_has_a_repository_or_a_remote_not_both`
  - `test_a_remote_url_on_another_host_is_refused` (`https://example.com/x.git` for `github`)
  - `test_location_is_the_path_or_the_url`
  - `test_hosting_and_team_settings_refuse_values_out_of_range` (one value just outside each bound)
  - `test_team_names` (`acme` ok; `Acme`, `-acme` and a 31-character name refused)
  - `test_teams_is_not_a_target_name`
  - `test_write_remote_target_round_trips` (load it back; `remote.id`, `branch` and `team` equal)
  - `test_target_names_from_awkward_repository_names`: `team_target_name("acme", "Payments API #2", set())` is `"acme-payments-api-2"`. A 70-character name is cut to match `TARGET_NAME`. A clash with `{"acme-web-shop"}` gives `"acme-web-shop-rkgdoe"` for id `R_kgDOExample`.

  (`team_target_name` is in `codetrail.team.names`, created in this task; its tests stay here.)
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/unit/test_config.py -q`. Expected: FAIL, the names aren't defined.
- [ ] **Step 3: Implement** the interfaces above in `config.py`, and `team_target_name(team: str, repository_name: str, taken: set[str], service_id: str = "") -> str` in `src/codetrail/team/names.py` (with `src/codetrail/team/__init__.py`, which stays empty). The `RemoteSettings` URL validator imports from `codetrail.hosting.names`, so create that module now with `Service`, `HOSTS` and `check_clone_url`. Task 3 adds the rest of it.
- [ ] **Step 4: Run the test and `just typecheck`.** Expected: PASS, and no type errors. `TargetConfig.repository` is optional now, so mypy lists every caller. Make each one fail clearly for a remote target ("remote targets arrive in Task 5") until Task 5 replaces them.
- [ ] **Step 5: Commit** (`feat(config): settings for remote targets, teams and hosting programs`).

### Task 3: the hosting interface, its runner and the fake

**Files:**
- Create: `src/codetrail/hosting/__init__.py`, `src/codetrail/hosting/names.py`, `src/codetrail/hosting/run.py`, `src/codetrail/hosting/fake.py`
- Modify: `tests/conftest.py` (`REAL_PROGRAMS` gains `gh`, `az`, `glab`; the guard also wraps `codetrail.hosting.run`'s calls)
- Test: `tests/unit/test_hosting_names.py`, `tests/unit/test_hosting_run.py`

**Interfaces — produces:**
- In `hosting/__init__.py`:
  - `class HostingError(CodetrailError)`
  - `@dataclass(frozen=True) HostingStatus(service: Service, installed: bool, signed_in: bool, account: str | None, fix: str | None)`, where `fix` is the command the reader runs
  - `@dataclass(frozen=True) Source(service: Service, name: str, repository_count: int | None)`
  - `@dataclass(frozen=True) HostedRepository(service: Service, source: str, id: str, name: str, default_branch: str | None, archived: bool, disabled: bool, pushed_at: datetime | None, size_kb: int | None, https_url: str, ssh_url: str, web_url: str)`, with `def clone_url(self, clone_over: str) -> str`
  - `class HostingService(Protocol)` with `service: Service`, `status()`, `sources()`, `repositories(source: str)` and `branches(repository: HostedRepository) -> list[str]`, as in design 24.3
- In `hosting/names.py`:
  - `HOSTS: dict[Service, tuple[str, str]]` (https host, ssh host)
  - `validate_source(service, name) -> str`
  - `validate_repository_name(service, name) -> str`
  - `check_clone_url(service, url) -> str`

  The patterns are:
  - GitHub: owner `[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})`, repository `[A-Za-z0-9._-]{1,100}`.
  - Azure DevOps: `<organization>/<project>`, organization `[A-Za-z0-9][A-Za-z0-9-]{0,49}`; project and repository names of 1 to 64 characters, with no control characters and none of `/\:*?"<>|;#$*{},+=[]`, not starting with `.` or `_`, and not ending with `.`.
  - GitLab: a path of segments `[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}`, joined by `/`, with no `..` and at most 20 segments.
- In `hosting/run.py`:
  - `TOKEN_VARIABLES: frozenset[str]`
  - `hosting_environment(environ: Mapping[str, str]) -> dict[str, str]`
  - `READS: dict[Service, tuple[tuple[str, ...], ...]]`, the argument prefixes each service may run, filled in from Task 1
  - `is_read(service, arguments: Sequence[str]) -> bool`
  - `run_read(service, program: str, arguments: Sequence[str], settings: HostingSettings, environ: Mapping[str, str] = os.environ) -> str`. It refuses when `is_read` is false, resolves the program with `codetrail.assistant.environment.resolve_program`, runs it with `codetrail.assistant.runner.run_command` from `/` with `hosting_environment`, refuses output longer than `max_output_bytes`, and raises `HostingError` naming the program and its last error line on a non-zero exit.
- In `hosting/fake.py`: `FakeHosting(service, statuses, sources, repositories, branches)` implements `HostingService` from plain data and records each call.

- [ ] **Step 1: Write the failing tests.**
  - `test_each_service_accepts_and_refuses_names`: for each service, two names it accepts and three it refuses (`..`, a leading `-`, a control character).
  - `test_clone_urls_must_be_https_or_ssh_on_the_service_host`: refuses `file://`, `ext::sh -c x`, `http://github.com/…`, `https://github.com.evil.example/…` and `https://user:pass@github.com/…`.
  - `test_token_variables_never_reach_the_program`: with every name in `TOKEN_VARIABLES` set, `hosting_environment` holds none of them, and keeps `HTTPS_PROXY` and `SSL_CERT_FILE`.
  - `test_relative_path_entries_are_dropped`
  - `test_a_command_outside_the_read_list_is_refused_before_it_runs`: `run_read("github", "gh", ["repo", "delete", "x"], …)` raises `HostingError`, and no process starts. A fake program records its runs (`tests/fixtures/programs.make_program`).
  - `test_every_read_prefix_is_a_known_read`: each entry of `READS` is in a hand-written list in the test, which is the reviewer's checklist. `api` entries must include `--method GET`.
  - `test_output_over_the_limit_is_refused`
  - `test_a_failing_program_names_itself_and_its_last_line`
  - `test_azure_names_with_spaces_are_one_argument`: a fake `az` that prints its `argv` as JSON shows `Payments API #2` as one element.
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/unit/test_hosting_names.py tests/unit/test_hosting_run.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** (`feat(hosting): a read-only runner for the reader's hosting programs, and a fake`).

### Task 4: the three adapters

**Files:**
- Create: `src/codetrail/hosting/github.py`, `azure_devops.py`, `gitlab.py`, and `src/codetrail/hosting/services.py` (`services_for(settings: GlobalConfig) -> dict[Service, HostingService]`)
- Test: `tests/unit/test_hosting_adapters.py`, `tests/live/test_hosting_live.py`

**Interfaces:**
- Consumes: `run_read` and the types from Task 3; the fixtures from Task 1.
- Produces: `GitHub(settings: HostingSettings)`, `AzureDevOps(settings)` and `GitLab(settings)`, each a `HostingService`.
  - `status()` never raises. A missing program gives `installed=False`, with a `fix` naming how to install it. A failed sign-in check gives `signed_in=False`, with `fix` set to `gh auth login`, `az login` or `glab auth login`.
  - `repositories()` reads at most `max_repositories_per_source` and validates every name and URL (Task 3). A repository whose name or URL fails validation is left out and logged by its id only.

- [ ] **Step 1: Write the failing tests.** Each adapter runs against a fake program that prints its fixture.
  - `test_<service>_parses_repositories`: every field, archived and disabled flags, `pushed_at` as an aware `datetime`, and `size_kb`.
  - `test_<service>_status_signed_out_names_the_fix`
  - `test_<service>_missing_program_is_not_installed`
  - `test_<service>_drops_a_repository_with_an_invalid_url`
  - `test_<service>_stops_at_the_repository_limit`
  - `test_<service>_branches`
  - Live (`@pytest.mark.live`): `test_installed_programs_answer_read_only` is skipped for a program that isn't installed. It checks only that `status()`, `sources()` and `repositories()` return without error, and prints no names.
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/unit/test_hosting_adapters.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** each adapter with the commands from Task 1. Each adds its argument prefixes to `READS`.
- [ ] **Step 4: Run** the unit tests, then `mise exec -- just test-live -k hosting`. Expected: unit PASS. Live: GitHub passes here, and `az` and `glab` are skipped as not installed.
- [ ] **Step 5: Commit** (`feat(hosting): GitHub, Azure DevOps and GitLab adapters`).

### Task 5: remote mirrors

**Files:**
- Modify: `src/codetrail/repo/git.py` (`run_git` gains `environment: Mapping[str, str] | None = None`; `None` keeps today's environment), `src/codetrail/repo/mirror.py`
- Create: `src/codetrail/repo/remote.py`
- Modify every caller of `target.repository`: `repo/refresh.py`, `repo/signal.py`, `update.py`, `server.py`, `agents/export.py`, `cli.py`. Each uses `check_containment` only when `target.repository` is set, `fetch_target` for the mirror, and `target.location` for display.
- Test: `tests/unit/test_remote_mirror.py`, `tests/unit/test_signal.py`, `tests/integration/test_remote_update.py`

**Interfaces — produces:**
- `mirror.refresh_mirror_from(mirror: Path, url: str, branch: str, *, git_options: Sequence[str] = (), environment: Mapping[str, str] | None = None) -> str` holds today's clone, fetch and tag logic. `refresh_mirror(mirror, repository, branch)` becomes `check_branch` plus `refresh_mirror_from(mirror, repository_url(repository), branch)`, with its behaviour unchanged.
- `remote.REMOTE_PROTOCOLS = ("https", "ssh")`
- `remote.protocol_options(protocols: Sequence[str]) -> list[str]`, giving the `-c protocol…` options from Global Constraints
- `remote.remote_git_environment(environ: Mapping[str, str]) -> dict[str, str]`
- `remote.refresh_remote_mirror(mirror: Path, url: str, branch: str, *, protocols: Sequence[str] = REMOTE_PROTOCOLS) -> str` checks the branch with `ls-remote`, then calls `refresh_mirror_from`. When git fails, it raises `CodetrailError`: "git couldn't fetch <url>. If it needs your sign-in, run: <fix>". The fix is `gh auth setup-git`, `glab auth login`, or "Git Credential Manager or an SSH key" for Azure DevOps, chosen by host. git's own message is never shown, since it can quote a URL with credentials.
- `remote.fetch_target(mirror: Path, target: TargetConfig) -> str` dispatches on `repository` or `remote`.

- [ ] **Step 1: Write the failing tests.**
  - `test_remote_git_gets_no_token_variables`, and keeps `SSH_AUTH_SOCK`, `GIT_SSL_CAINFO` and the proxy variables.
  - `test_file_ext_and_http_are_refused_by_default`: `refresh_remote_mirror` with `file://`, `ext::` and `http://` URLs fails before cloning.
  - `test_a_remote_mirror_clones_and_fetches`: a fixture repository is made servable with `git update-server-info`, served by `http.server.ThreadingHTTPServer` on `127.0.0.1` (git's dumb HTTP protocol), and fetched with `protocols=("http",)`. A first refresh clones, a second fetches a new commit, and tags behave as for local mirrors.
  - `test_a_fetch_that_needs_a_sign_in_names_the_fix`: the server answers 401. The error names `gh auth setup-git` for a github.com URL, contains no `@` or password, and git never prompts (the test fails on a timeout).
  - `test_local_targets_are_unchanged`: the existing mirror tests pass untouched.
  - `test_the_signal_fetches_a_remote_target` (in `test_signal.py`)
  - `test_a_remote_target_updates_with_the_fake_assistant` (integration): a remote target's update records facts and writes the guide, and the secret-filter fixtures pushed to the remote never reach `source/`.
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/unit/test_remote_mirror.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above and switch the callers.
- [ ] **Step 4: Run** `mise exec -- just test-quick` and `mise exec -- just typecheck`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(repo): mirror remote targets over https or ssh with the reader's git sign-in`).

### Task 6: teams, following and the `team` commands

**Files:**
- Create: `src/codetrail/team/follow.py`, `src/codetrail/team/settings.py`, `src/codetrail/team/sync.py`
- Modify: `src/codetrail/remove.py`, `src/codetrail/lock.py` (`team_in_use`, `team_removal`, on the team's settings file, like the target's), `src/codetrail/cli.py`
- Test: `tests/unit/test_team_follow.py`, `tests/unit/test_team_sync.py`, `tests/unit/test_remove.py`, `tests/unit/test_cli.py`

**Interfaces — produces:**
- `follow.followed(source: TeamSource, listed: list[HostedRepository], already: set[str], now: datetime, active_days: int) -> list[HostedRepository]`, where `already` holds the ids the team follows now. It applies the following rule in Global Constraints. With `chosen`, it follows exactly `source.chosen` among those listed.
- `settings.write_team(paths, name) -> Path` creates an empty team file, `open("x")`.
- `settings.save_selection(paths, name, selection: TeamSource) -> None` replaces the team's entry for that service and source, and writes to a temporary file that is then renamed.
- `settings.team_targets(paths, name) -> dict[str, TargetConfig]` maps target name to settings, for every target whose `team` is `name`.
- `sync.sync_team(paths, name, services: Mapping[Service, HostingService], now: datetime, branches: Mapping[str, str] = {}) -> TeamSync`, with `TeamSync(added: list[str], stopped: list[str], unreachable: list[str])`. It lists each source, writes a remote target for each newly followed repository (the branch comes from `branches[id]`, else the default branch), and reports followed targets whose repository is archived or gone. A source whose service fails goes to `unreachable` and doesn't stop the others. It runs under `team_in_use`.
- `remove.remove_target` refuses when the target's `team` still follows its `remote.id`, naming the team.
- `remove.remove_team(paths, name, confirm: Callable[[list[Path]], bool]) -> bool` lists the team's file, its data folder and each of its targets' locations, asks, and removes the targets with `remove_target` and then the team. It holds `team_removal`.
- CLI:
  - `codetrail team add <name>` writes the team and then serves it, opening at `/team/add`.
  - `codetrail team list` prints each team with its sources and target count.
  - `codetrail team remove <name> [--yes]`
  - `codetrail team serve <name> [--no-browser]` (wired up in Task 8)

- [ ] **Step 1: Write the failing tests.**
  - `test_follow_all_takes_active_repositories_only`: 600 listed, 40 pushed within 90 days, and 40 followed.
  - `test_archived_disabled_and_empty_are_never_followed`
  - `test_forks_and_templates_count`
  - `test_a_quiet_followed_repository_stays_followed`
  - `test_left_out_ids_are_not_followed`
  - `test_chosen_follows_exactly_the_chosen`
  - `test_sync_writes_targets_and_reports_stopped`
  - `test_sync_matches_a_renamed_repository_by_id`
  - `test_one_unreachable_source_does_not_stop_the_others`
  - `test_target_remove_refuses_while_the_team_follows_it`
  - `test_target_remove_works_once_left_out`
  - `test_team_remove_lists_asks_and_removes_everything`
  - `test_team_remove_refuses_while_served`
  - `test_team_list_prints_sources_and_counts`
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `mise exec -- just test-quick`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(team): teams that follow a source's active repositories`).

### Task 7: every link through `site_path`, so a guide can be mounted

The page builds absolute links in templates, in JavaScript and in Python (`diagrams.py`, `render.py`, `repository_view.py`). Under `/repos/<target>/`, each one must carry the prefix. `/static/…` stays at the root.

**Files:**
- Create: `src/codetrail/web/links.py`
- Modify: `src/codetrail/web/app.py` (split it into `create_target_app`, unwrapped, and `create_app`, which wraps it), `security.py`, `diagrams.py`, `render.py`, `repository_view.py`, every template under `web/templates/` with a root-relative link, and `static/js/api.js`, `ask.js`, `learning.js`, `main.js`, `palette.js`, `repository.js`, `shortcuts.js`, `sidebar.js`, `update.js`
- Test: `tests/api/test_mounted_links.py`, `tests/api/test_security.py`

**Interfaces — produces:**
- `links.BASE_PATH: ContextVar[str]` (default `""`)
- `links.site_path(path: str) -> str` returns `BASE_PATH.get() + path`, for paths starting with `/`.
- `links.BasePathMiddleware(app)` sets `BASE_PATH` from `scope["root_path"]` for the request.
- `site_path` is a Jinja global. `base.html` gains `<meta name="codetrail-base" content="{{ site_path('') }}">`.
- `api.js` exports `sitePath(path)`. `post()` and `getJSON()` prefix their URL themselves, and every other root-relative URL in the scripts goes through `sitePath`.
- `create_target_app(paths, name, session, settings, …) -> ASGIApp` returns `BasePathMiddleware(TargetInUseMiddleware(fastapi_app))`. Its `TargetInUseMiddleware` compares the route path (the path without `root_path`) with `OPEN_PATHS`.
- `create_app(...)` keeps its signature and returns `SecurityMiddleware(create_target_app(...))`.
- `security.route_path(path: str) -> str` drops a leading `/repos/<target-name>` (matching `TARGET_NAME`). `SecurityMiddleware` uses it for `TOKEN_READS`, `OPEN_PATHS` and `D2_WORKER`. `/static/` is matched only at the root.
- Any cache of rendered HTML or link-bearing JSON is keyed by `BASE_PATH.get()` as well.

- [ ] **Step 1: Write the failing tests.**
  - `test_every_link_stays_under_the_prefix`: mount `create_target_app` for a guide fixture at `/repos/web-shop` in a Starlette app behind one `SecurityMiddleware`. Fetch home, a page, an area, a fact, a source file, `/digests`, `/progress`, `/system`, `/repository`, `/documentation`, `/decisions` and `/search?q=x`. Every `href`, `src`, `action` and `data-*` attribute holding a URL either starts with `/repos/web-shop/`, `/static/` or `#`, or is an `http(s)://` link out.
  - `test_unmounted_links_are_unchanged`: the same crawl at `/` matches today's links.
  - `test_token_reads_under_a_prefix_need_the_token`: `/repos/web-shop/bridge/answers` and `/repos/web-shop/search/results` without `X-Codetrail-Token` get 403.
  - `test_writes_under_a_prefix_need_origin_and_token`
  - `test_the_meta_base_is_the_prefix`
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/api -q`. Expected: the new tests FAIL, and the old ones pass.
- [ ] **Step 3: Implement** the interfaces above, replacing each root-relative link.
- [ ] **Step 4: Run** `mise exec -- just test-quick` and `mise exec -- just test-browser`. Expected: PASS. The single-target page is unchanged.
- [ ] **Step 5: Commit** (`refactor(web): build every link with site_path so a guide can be mounted`).

### Task 8: the team server and the plain team page

**Files:**
- Create: `src/codetrail/team/server.py`, `src/codetrail/web/templates/team.html`
- Modify: `src/codetrail/cli.py` (`team serve`), the gettext catalogs (`just catalogs`)
- Test: `tests/api/test_team_server.py`

**Interfaces:**
- Consumes: `create_target_app` and `site_path` (Task 7); `team_targets` and `sync_team` (Task 6); `behind` (Task 5); `services_for` (Task 4).
- Produces:
  - `create_team_app(paths, name, session, settings, services: Mapping[Service, HostingService], hold: Callable[[str], None], locales: Path | None = None) -> ASGIApp`. It is a FastAPI app with `/login`, `GET /` (the team page) and `/static` at the root, plus one `Mount(f"/repos/{target}", create_target_app(...))` per followed target. All of it is wrapped once in `SecurityMiddleware`. `hold(target)` is called before a target is mounted at runtime.
  - `serve_team(paths, name, open_browser: bool, start_path: str = "/") -> None` holds `team_in_use` and every followed target's `target_in_use` in one `ExitStack` for as long as it runs, and its `hold` enters new ones. It prints and opens the single-use sign-in link, followed by `start_path`. `/login` gains an optional `next` that must be one of `/` and `/team/add`.
  - **The team page:** the team's name and its sources, then one row per followed target. Each row has the repository's name, its branch, and its free signal: merges and areas since the last update, "Not updated yet" for a target with no snapshot, "Updating" while its update runs, or why it couldn't be reached. Each row links to `/repos/<target>/`. Below are the archived or gone repositories, and a button to add repositories. Signals are computed in a thread pool of `hosting.concurrency` and cached per target for `signal.cache_seconds`.

- [ ] **Step 1: Write the failing tests.**
  - `test_team_page_lists_each_target_with_its_signal`
  - `test_one_unreachable_repository_does_not_block_the_page`: one target's mirror URL points at a closed port, and the page renders the others with that one's reason.
  - `test_each_guide_is_served_under_its_prefix`
  - `test_serving_holds_every_target`: `target remove` refuses while `serve_team` runs.
  - `test_login_next_is_limited_to_known_paths`
  - `test_team_page_escapes_names_from_the_service`: a repository named `<script>` is rendered as text.
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/api/test_team_server.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above, adding the team page's strings to the catalogs.
- [ ] **Step 4: Run** `mise exec -- just test-quick`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(team): serve a team's guides under one login, with a plain team page`).

### Task 9: the add-repositories screen

**Files:**
- Create: `src/codetrail/team/routes.py`, `src/codetrail/team/refresh.py`, `src/codetrail/web/templates/team_add.html`, `src/codetrail/web/static/js/team_add.js`, and the screen's styles in `static/css/components.css`
- Modify: `src/codetrail/team/server.py` (include the router), `src/codetrail/web/security.py` (`TOKEN_READS` gains `/team/hosting/sources`, `/team/hosting/repositories` and `/team/refresh/status`), the catalogs
- Test: `tests/api/test_team_add.py`, `tests/browser/test_team_add.py`

**Interfaces:**
- Consumes: `services_for` (Task 4); `followed`, `save_selection` and `sync_team` (Task 6); `refresh_source` and `run_update(…, facts_only=True)` (existing); `create_team_app`'s `hold` (Task 8).
- Produces:
  - `GET /team/add` is the screen.
  - `GET /team/hosting/sources` returns `{"services": [{service, installed, signed_in, account, fix}], "sources": [{service, name, repository_count}]}`.
  - `GET /team/hosting/repositories?service=&source=` returns `{"repositories": [{id, name, default_branch, archived, pushed_at, size_kb, active}], "following": {follow, left_out, chosen, clone_over} | null}`. The source passes `validate_source`.
  - `GET /team/hosting/branches?service=&source=&id=` returns `{"branches": [...]}`.
  - `POST /team/repositories` takes `SourceSelection(service, source, follow, clone_over, left_out: list[str], chosen: list[str], branches: dict[str, str])`, with `extra="forbid"` and every list at most `max_repositories_per_source` long. It lists the source again server-side, refuses an id not in that listing and a branch that fails `validate_branch_name` or starts with `-`, and only then saves, syncs, mounts and starts the refresh job. It returns `{"added": [...], "refresh": true}`.
  - `refresh.TeamRefreshJob(paths, targets: list[str], concurrency: int)` runs `run_update(paths, target, facts_only=True)` for each new target, `concurrency` at a time, recording steps (`started`, `done`, `failed` with the error's message) for `GET /team/refresh/status?after=N`. One job runs per team, and it stops on server shutdown the way `UpdateJob` does.
  - **The screen** (design 24.6, option A): service chips with their status; a source picker; the "Follow the whole source" switch, whose label says how many repositories are left out; a filter field; "Active in the last N days" (on) and "Show archived"; and rows with a checkbox, name, last push, size and a branch picker that loads branches when opened. "Add N repositories" posts, then shows the refresh steps and links to the team page. All text comes from the catalogs, and every service string is set with `textContent`.

- [ ] **Step 1: Write the failing tests.**
  - `test_sources_list_each_service_status_and_fix`
  - `test_repositories_are_listed_with_activity`
  - `test_unknown_ids_and_bad_branches_are_refused`: an id not listed, a branch `--upload-pack=x`, and an extra key are refused with 422 or 400, and no file is written.
  - `test_saving_writes_team_and_targets_and_starts_the_refresh`
  - `test_hosting_reads_need_the_token`
  - Browser (`@pytest.mark.browser`): `test_follow_all_then_leave_one_out`, `test_switch_off_keeps_the_ticked_rows`, `test_branch_picker_loads_and_saves`, `test_add_screen_has_no_accessibility_violations`.
- [ ] **Step 2: Run** `mise exec -- uv run pytest tests/api/test_team_add.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run** `mise exec -- just test-quick` and `mise exec -- just test-browser -k team`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(team): the add-repositories screen`).

### Task 10: documents, the whole check and the security review

**Files:**
- Modify: `docs/getting-started.md` (a "Following your team's repositories" section: install and sign in to `gh`, `az` or `glab`; let git use the sign-in; `codetrail team add`; what the plain team page shows; that the catch-up arrives in Phase 22), `README.md` (current state), `CHANGELOG.md` (an Unreleased line), `docs/security/review-checklist.md` (the hosting programs' environment and read-only list, remote transports, the mounted routes and `route_path`, the team routes), `docs/adr/0020-hosting-services-through-their-own-programs.md` (tick the checklist item), `docs/design/2026-10-05-codetrail-design.md` (Phase 21's row loses "(designed)")

- [ ] **Step 1: Update the documents** listed above.
- [ ] **Step 2: Run** `mise exec -- just ci`. Expected: PASS.
- [ ] **Step 3: Commit** (`docs(docs): teams in the getting-started guide, README, changelog and checklist`).
- [ ] **Step 4: Review the branch** with the `security-reviewer` agent, giving it the branch name and the test results. Fix every critical and high finding with new commits and review again. Fix medium findings, or record why not in the merge message.
- [ ] **Step 5: Finish** with `git flow feature finish`, as AGENTS.md shows, with the verdict in the merge message. Then stop and report what landed on `develop`. The founder pushes.
