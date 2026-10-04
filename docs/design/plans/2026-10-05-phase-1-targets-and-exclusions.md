# Phase 1: Targets and exclusions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `codetrail target add` registers a repository, and `codetrail files <target>` refreshes Codetrail's own mirror and materialized sources and lists exactly what Codetrail can see, with secrets, ignored files and gitleaks findings gone, while the target repository is never changed in any way.

**Architecture:** `config` loads and validates the global and per-target TOML files with pydantic and knows the XDG folders. `repo` owns everything that touches a target: a bare mirror cloned and fetched over the `file://` transport (no hardlinks, no command run inside the target's working tree), the three exclusion layers, the materialized `source/` built into `source.next/` and swapped in, and filtered logs and diffs with gitleaks scanning their text.

**Tech Stack:** Python 3.14, pydantic, pathspec (ADR 0001), gitleaks 8.30 (ADR 0002), git.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 2.3, 3, 9, 10, 11.

## Global Constraints
- The target repository is never written: no command runs with the target as its working directory or git directory except through `git clone`/`git fetch`/`git ls-remote` over `file://` (which run `upload-pack`, read-only). No hardlink is ever made to its files.
- Built-in secret patterns can't be overridden by any ignore line; `*.example` files are not secrets; `.terraform/` excludes everything under it.
- gitleaks runs with Codetrail's own config (`--config`, extending the defaults), `--ignore-gitleaks-allow`, `--gitleaks-ignore-path /dev/null`, `--redact`, with `GITLEAKS_CONFIG` and `GITLEAKS_CONFIG_TOML` cleared, so nothing in the target can switch it off. Missing or failing gitleaks fails the refresh.
- Symlinks and submodules are never materialized.
- Output and logs carry paths and rule ids only, never file contents or secret values.
- Target names match `^[a-z0-9][a-z0-9-]{0,62}$`; the target's path and Codetrail's folders must not contain each other.

## Review Focus
1. A target that commits its own `.gitleaks.toml` allowlisting everything, a `.gitleaksignore`, or a `gitleaks:allow` comment must still have its secret flagged — test in Task 4.
2. A diff whose commit *removed* a secret must not reveal it — test in Task 5.
3. A path like `../x` or an absolute path in an ignore file, or a tree entry named `..`, must not escape `source/` — materialization refuses unsafe paths (Task 3).
4. The target repository is byte-for-byte unchanged after `target add`, `files` run twice, and a refresh after new commits, including link counts of its object files — Task 6's end-to-end test.
5. A refresh that fails midway (gitleaks missing) leaves the previous `source/` intact — Task 4.

---

### Task 1: Configuration and folders

**Files:** Create `src/codetrail/errors.py`, `src/codetrail/config.py`, `tests/unit/test_config.py`. Modify `pyproject.toml` (pydantic).

**Interfaces — produces:**
```python
class CodetrailError(Exception): """An error with a message fit to show the user."""

@dataclass(frozen=True)
class Paths:
    config_dir: Path; data_dir: Path; state_dir: Path
    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> Paths   # XDG_*_HOME, else ~/.config etc.
    def target_file(self, name: str) -> Path        # config_dir/targets/<name>.toml
    def ignore_file(self, name: str) -> Path        # config_dir/targets/<name>.ignore
    def target_data(self, name: str) -> Path        # data_dir/<name>
    def target_state(self, name: str) -> Path       # state_dir/<name>

class ToolsSettings(BaseModel): gitleaks: str = "gitleaks"
class GlobalConfig(BaseModel): tools: ToolsSettings  (extra="forbid")
class TargetConfig(BaseModel): repository: Path; branch: str  (extra="forbid"; repository expanded with ~)

def validate_target_name(name: str) -> str
def load_global(paths: Paths) -> GlobalConfig                 # absent file -> defaults
def load_target(paths: Paths, name: str) -> TargetConfig      # absent -> CodetrailError naming `target add`
def write_target(paths: Paths, name: str, repository: Path, branch: str) -> Path   # refuses to overwrite
```
**Tests:** XDG variables respected and defaults under the home folder; bad names (`../x`, `Hamesh`, `a b`, empty, 64 chars) refused; unknown keys refused naming the key; missing target file message names `codetrail target add`; written TOML round-trips (including a path with `"` and non-ASCII); `write_target` refuses an existing target; the repository may not be inside `data_dir` nor contain it.

### Task 2: Exclusion rules

**Files:** Create `src/codetrail/repo/__init__.py`, `src/codetrail/repo/rules.py`, `tests/unit/test_rules.py`. Modify `pyproject.toml` (pathspec).

**Interfaces — produces:**
```python
class Reason(StrEnum): SECRET_PATTERN = "secret pattern"; IGNORED = "ignore rules"; GITLEAKS = "gitleaks"
BUILTIN_FILE_PATTERNS: tuple[str, ...]      # .env, .env.*, .dev.vars, *.p8, *.p12, *.pem, *.keystore, *.jks, *.tfstate,
                                            # *.tfstate.backup, *.tfvars, *.tfvars.json, *.key, *.pfx, id_rsa*, id_ed25519*,
                                            # *.ppk, .netrc, .npmrc, .pypirc   (then "!*.example")
BUILTIN_DIRECTORY_PATTERNS: tuple[str, ...] # .terraform/
class ExclusionRules:
    def __init__(self, ignore_lines: Sequence[str]) -> None
    def reason(self, path: str) -> Reason | None    # built-ins first, then ignore lines
```
**Tests:** every built-in pattern at the root and nested; `.env.example` and `services/api/.env.example` visible; `.terraform/x.example` still excluded; `!.env` and `!**/*.pem` in ignore lines don't re-include; gitignore semantics: `docs/` excludes nested files, `/build` anchors to root only, `**/*.png`, `a/**/b`, negation re-includes a file excluded by an earlier ignore line; comments and blank lines ignored.

### Task 3: Mirror and materialization

**Files:** Create `src/codetrail/repo/git.py`, `src/codetrail/repo/mirror.py`, `src/codetrail/repo/source.py`, `tests/fixtures/__init__.py`, `tests/fixtures/repos.py`, `tests/unit/test_mirror.py`, `tests/unit/test_source.py`.

**Interfaces — produces:**
```python
def run_git(arguments: Sequence[str], *, git_dir: Path | None = None, input: bytes | None = None) -> bytes
    # git with GIT_TERMINAL_PROMPT=0, never a shell; CodetrailError with git's stderr on failure

def repository_url(path: Path) -> str                      # file:// URL of a checkout (resolved)
def check_branch(repository: Path, branch: str) -> None    # git ls-remote over file://; CodetrailError if absent
def refresh_mirror(mirror: Path, repository: Path, branch: str) -> str
    # clone --bare --no-local over file:// on first use, else fetch +refs/heads/<branch>; returns the head commit

@dataclass(frozen=True)
class TreeEntry: mode: str; blob: str; path: str
def list_tree(mirror: Path, commit: str) -> list[TreeEntry]          # ls-tree -r -z --full-tree
def read_file_at(mirror: Path, commit: str, path: str) -> bytes | None

@dataclass(frozen=True)
class Excluded: path: str; reason: Reason; rule: str | None = None   # rule: gitleaks rule id

@dataclass(frozen=True)
class SourceManifest:
    commit: str; files: dict[str, str]; excluded: list[Excluded]   # files: path -> blob
    def save(self, path: Path) -> None; @classmethod def load(cls, path: Path) -> SourceManifest | None

def build_source(mirror: Path, commit: str, rules: ExclusionRules, scanner: SecretScanner, data_dir: Path) -> SourceManifest
    # writes data_dir/source.next/, scans it, removes flagged files, swaps it into data_dir/source/,
    # saves data_dir/source.json; on any failure removes source.next/ and leaves source/ as it was
```
Materialization writes regular files (modes 100644, 100755) only; symlinks (120000) and submodules (160000) are skipped silently (counted). Each path is checked: no absolute paths, no `..` or `.git` components, and the resolved destination must be inside `source.next/`.

**Fixture helper** `tests/fixtures/repos.py`: `make_repository(root, commits: list[dict[str, str | bytes | Symlink | None]], branch="develop") -> Path` builds a checkout with `git init` and one commit per dict (None deletes), with `GIT_CONFIG_GLOBAL=/dev/null`, fixed author and dates. `snapshot_tree(path) -> dict[str, tuple]` records every file's relative path, size, mtime_ns, inode, link count and sha256, for the read-only assertion. `fake_github_token() -> str` assembles a token-shaped string at runtime.

**Tests:** first refresh clones, second fetches new commits and returns the new head; a missing branch is a `CodetrailError`; the checkout is unchanged (snapshot equal) after both; symlinks and submodule entries never appear in `source/`; excluded files are absent and listed with their reasons; `source.json` round-trips; a tree path with `..` is refused.

### Task 4: gitleaks scanning

**Files:** Create `src/codetrail/repo/secrets.py`, `src/codetrail/repo/gitleaks.toml`, `tests/unit/test_secrets.py`.

**Interfaces — produces:**
```python
@dataclass(frozen=True)
class Finding: path: str; rule: str; line: int
class SecretScanner:
    def __init__(self, executable: str) -> None          # CodetrailError at first use if not found
    def scan_directory(self, root: Path) -> list[Finding] # paths relative to root
    def scan_text(self, text: str) -> list[Finding]       # gitleaks stdin; path is ""
```
`gitleaks.toml` is `[extend]\nuseDefault = true` plus a title. Commands: `gitleaks dir <root> --config <ours> --report-format json --report-path <tmp> --redact --no-banner --exit-code 0 --ignore-gitleaks-allow --gitleaks-ignore-path /dev/null --log-level error`, `gitleaks stdin` likewise, with `GITLEAKS_CONFIG`/`GITLEAKS_CONFIG_TOML` removed from the environment.
**Tests (real gitleaks from mise):** a fake token in `config.py` is found with its rule id; a clean file isn't; a scanned tree containing a `.gitleaks.toml` that allowlists everything, a `.gitleaksignore` listing the finding, and a `gitleaks:allow` comment still yields the finding; a missing executable raises `CodetrailError` (and `build_source` leaves the previous `source/` intact); `scan_text` finds a token in a diff; no finding carries the secret value.

### Task 5: Filtered history

**Files:** Create `src/codetrail/repo/history.py`, `tests/unit/test_history.py`.

**Interfaces — produces:**
```python
@dataclass(frozen=True)
class Commit: sha: str; author: str; date: str; subject: str; body: str; files: list[str]; is_merge: bool
@dataclass(frozen=True)
class FileDiff: path: str; status: str; patch: str | None    # None when withheld
def commits_between(mirror: Path, start: str | None, end: str, visible: Callable[[str], bool], scanner: SecretScanner) -> list[Commit]
def merges_between(mirror: Path, start: str, end: str) -> int     # first-parent merges only
def diff_between(mirror: Path, start: str, end: str, visible: Callable[[str], bool], scanner: SecretScanner) -> list[FileDiff]
```
`visible(path)` is true when the file is not excluded at `end` (rules plus the manifest's gitleaks set). Commit messages flagged by `scan_text` become subject `[withheld: gitleaks flagged this message]` with an empty body. Diffs are scanned once as one text with known line offsets; a file whose patch contains a finding gets `patch=None`.
**Tests:** a commit touching only `.env` appears with no files; excluded paths never appear in files or diffs; a commit that removes a planted token yields that file's patch withheld; a message containing a token is withheld; merges are counted on the first parent only.

### Task 6: The `target add` and `files` commands, locking, and an end-to-end test

**Files:** Create `src/codetrail/repo/refresh.py`, `src/codetrail/lock.py`, `tests/unit/test_lock.py`, `tests/e2e/test_files_command.py`. Modify `src/codetrail/cli.py`, `tests/unit/test_cli.py`.

**Interfaces — produces:**
```python
@contextmanager
def target_lock(paths: Paths, name: str) -> Iterator[None]     # fcntl.flock LOCK_EX|LOCK_NB on data/<name>/update.lock;
                                                               # CodetrailError "already updating" when held
def load_rules(paths: Paths, name: str, mirror: Path, commit: str) -> ExclusionRules
    # target's .codetrailignore at the commit, then the founder's ignore file
def refresh_source(paths: Paths, name: str) -> SourceManifest  # lock, mirror, rules, scanner, build_source
```
CLI: `codetrail target add <name> <path> [--branch develop]` (validates the name, that `<path>/.git` exists, the branch via `check_branch`, then writes the file and prints where); `codetrail files <name>` prints each visible path, then `Excluded:` lines `<reason>\t<path>` (with the rule id for gitleaks), then a summary line `N visible, M excluded (a secret pattern, b ignore rules, c gitleaks), commit <short>`. Errors print `codetrail: <message>` and exit 1.
**Tests:** a second lock holder gets "already updating"; the lock is released after an exception; CLI end to end on the hostile fixture (`.env`, `prod.tfvars`, `key.pem`, a symlink to `/etc/passwd`, a `.codetrailignore` with `docs/private/`, a founder ignore file with `**/*.png`, a planted token in `app/settings.py`, a `.env.example`): exact visible list, each exclusion with its reason, and the checkout's snapshot unchanged after `target add` and two `files` runs with a new commit between them.

### Task 7: Documents and finish
- README roadmap (Phase 1 done; `files` usable), spec refinements: section 3.2 (clone and fetch over `file://`, `source/` rebuilt fully each refresh, scan of the whole tree), 3.4 (Codetrail's gitleaks config and flags; diffs and messages scanned), 3.6 (removed: messages are now scanned), 9 (`[tools] gitleaks`). Then `just ci`, security review, finish.
