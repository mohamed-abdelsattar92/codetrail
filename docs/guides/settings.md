# Settings

Every setting you can set, its default and what it does. A setting you leave out takes its default, so most readers never edit a file. Codetrail checks every value when it loads a file and refuses an unknown key by name, so a typo is reported, not ignored.

**Before you start:** nothing. You edit these files by hand in any text editor; Codetrail only creates the target and team files (see [Your first guide](first-guide.md) and [Teams](teams.md)).

## Where the files live

| File | Holds |
|---|---|
| `~/.config/codetrail/config.toml` | The global settings, for every target. You create it. Without it, every default applies. |
| `~/.config/codetrail/targets/<name>.toml` | One target's settings. `codetrail target add` writes it with the repository and branch. |
| `~/.config/codetrail/targets/<name>.ignore` | The target's own exclusions, in gitignore syntax (see [What Codetrail reads](what-codetrail-reads.md)). |
| `~/.config/codetrail/teams/<name>.toml` | One team's settings (see [Teams](teams.md)). |
| `~/.local/share/codetrail/<name>/` | A private copy of the repository's allowed files, the facts, the guide (its own git repository) and `agents/`. Not settings; listed so you know where things are. |
| `~/.local/state/codetrail/` | Temporary folders of the secret scan. |

The `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` variables move these folders. A relative value is ignored.

The server's host is always `127.0.0.1` and can't be changed.

## Edit a setting

1. Open the file in a text editor. For a global setting, that is `~/.config/codetrail/config.toml`; create it if it doesn't exist.
2. Add the section and the key. For example:

   ```toml
   [server]
   port = 9000
   ```

3. Run any Codetrail command again. It reports a bad value or an unknown key with the file's name and the key.

## Global settings (`config.toml`)

### `[tools]`

| Setting | Default | What it does |
|---|---|---|
| `gitleaks` | `"gitleaks"` | The gitleaks program: a name on your `PATH`, or an absolute path. |
| `gitleaks_timeout_seconds` | `600` | How long each gitleaks run, and each `mise which`, may take (above 0, at most 86400). |

### `[source]`

| Setting | Default | What it does |
|---|---|---|
| `max_file_bytes` | `50000000` | A committed file larger than this is hidden as `too large`. |
| `max_inflated_bytes` | `50000000` | The most text gitleaks reads from one compressed file; more is hidden as `unscannable`. |
| `max_ignore_lines` | `200` | The most lines with a pattern read from a repository's own `.codetrailignore`. |
| `max_ignore_bytes` | `65536` | The largest size of a repository's own `.codetrailignore`. |
| `max_ignore_match_seconds` | `60` | The time allowed to match paths against the ignore rules while a repository has its own `.codetrailignore`. |

### `[assistant]` and `[providers]`

| Setting | Default | What it does |
|---|---|---|
| `[assistant] retry_attempts` | `2` | How many times a call that failed without using anything (a rate limit, an overload, a network error) is retried (0 to 5). |
| `[providers] max_output_bytes` | `50000000` | A provider program that writes more than this to its output, or its error output, is stopped. |
| `[providers.claude_code] command` | `"claude"` | The Claude Code program. |
| `[providers.claude_code] auth` | `"subscription"` | `"subscription"` refuses an API-account sign-in; `"api_key"` passes `ANTHROPIC_API_KEY` to `claude` and you are billed by the API. |
| `[providers.claude_code] timeout_seconds` | `900` | How long one Claude Code call may run. |
| `[providers.claude_code] hook_timeout_seconds` | `30` | The time limit of the read-only guard hook Codetrail gives Claude Code. |
| `[providers.codex] command` | `"codex"` | The Codex program. |
| `[providers.codex] auth` | `"subscription"` | `"subscription"` or `"api_key"`, as for Claude Code. |
| `[providers.codex] max_tokens_per_call` | `400000` | The most tokens one Codex call may use. |
| `[providers.codex] timeout_seconds` | `900` | How long one Codex call may run. |
| `[providers.local] base_url` | `"http://127.0.0.1:11434/v1"` | The local model's server (Ollama). LM Studio is `http://127.0.0.1:1234/v1`. Only `127.0.0.1`, `::1` and `localhost`, with no user name, are allowed. |
| `[providers.local] max_turns` | `30` | The most turns one local call takes. |
| `[providers.local] max_tokens_per_call` | `200000` | The most tokens one local call may use. |
| `[providers.local] timeout_seconds` | `900` | How long one local call may run. |
| `[providers.local] max_read_bytes` | `200000` | The most bytes the local model reads from one file. |

Choosing a provider for each kind of call is in [Assistants](assistants.md).

### `[prices]` and `[estimates]`

`[prices]` holds USD per million tokens at API list prices. It is used for estimates and budgets only. It adds to the built-in prices, which are:

| Model | Input | Output | Cached input |
|---|---|---|---|
| `claude-opus-5-5` | 4.0 | 20.0 | 0.4 |
| `claude-sonnet-5-5` | 2.0 | 10.0 | 0.2 |

Add a model Codetrail has no price for, or replace a price, with a table of its own. `cached_input` is optional:

```toml
[prices."some-model"]
input = 1.25
output = 10.0
```

A model with no price shows **no price configured** in an estimate.

`[estimates]` sets the starting tokens per call, used until Codetrail has a history of its own. Each is `{ input = …, output = … }`:

| Setting | Default input | Default output |
|---|---|---|
| `plan` | 60000 | 8000 |
| `write` | 120000 | 6500 |
| `revise` | 40000 | 2500 |
| `digest` | 40000 | 3500 |
| `answer` | 30000 | 1500 |
| `grade` | 4000 | 500 |
| `overview` | 15000 | 1500 |

`[estimates] history_size` (default `20`, at least 3) is how many of your recent calls an estimate uses once it has a history.

### `[server]`, `[ui]` and `[signal]`

| Setting | Default | What it does |
|---|---|---|
| `[server] port` | `8765` | The port `codetrail serve` listens on (1024 to 65535). |
| `[server] login_code_ttl_seconds` | `60` | How long the sign-in link works. |
| `[server] session_minutes` | `480` | How long a session lasts. |
| `[server] update_cooldown_seconds` | `300` | How long after an update finishes before the page starts another. |
| `[server] estimate_ttl_seconds` | `300` | How long the page keeps an estimate waiting for **Go ahead**. |
| `[server] update_log_lines` | `200` | The update panel's steps the server keeps. |
| `[ui] default_language` | `"en"` | The interface language before you choose one. |
| `[ui] max_pins` | `9` | The most pinned pages the sidebar keeps (1 to 50). |
| `[signal] cache_seconds` | `60` | How long the "behind" check is kept. |

### `[bridge]`, `[search]` and `[learn]`

| Setting | Default | What it does |
|---|---|---|
| `[bridge] max_question_chars` | `4000` | The longest question the Ask panel sends (up to 20000). |
| `[bridge] max_turns` | `20` | The most turns one answer takes. |
| `[bridge] max_budget_usd` | `1.0` | The most one answer may cost, at API prices. |
| `[bridge] max_session_answers` | `20` | The unsaved answers kept per session (up to 200). |
| `[search] max_query_chars` | `200` | The longest search (up to 1000). |
| `[search] max_results` | `20` | The most results a search shows (up to 100). |
| `[learn] grading_cooldown_seconds` | `10` | The wait between two graded answers. `0` removes it. |
| `[learn] max_budget_usd` | `0.25` | The most one grading call may cost. |

### `[extract]`, `[diagrams]` and `[agents]`

| Setting | Default | What it does |
|---|---|---|
| `[extract] max_file_bytes` | `1000000` | A larger file is skipped when reading facts, with a warning. |
| `[extract] max_attribute_chars` | `300` | Text a fact takes from a file is cut to this length (at least 10). |
| `[extract] max_tsconfig_paths` | `100` | The path patterns and targets read from a `tsconfig`. |
| `[extract] max_workflow_steps` | `5000` | The steps read from one workflow file. |
| `[extract] max_resource_paths` | `20` | The path attributes kept on one Terraform resource. |
| `[diagrams] max_nodes` | `25` | The most nodes one diagram draws. |
| `[agents] max_map_words` | `2500` | The most words in the repository map for coding agents (500 to 20000). |
| `[agents] max_brief_words` | `300` | The most words in one brief (50 to 2000). |
| `[agents] key_files_per_part` | `5` | Key files listed for each part (0 to 20). |
| `[agents] recent_digests` | `5` | Recent digests the map lists (0 to 50). |
| `[agents] max_briefs_per_call` | `3` | The most briefs one tool call returns (1 to 10). |
| `[agents] max_response_words` | `4000` | The most words one tool call returns (500 to 20000). |

### `[metrics]`

The Documentation page and the Repository page use these (see [Exploring the code](exploring-the-code.md)).

| Setting | Default | What it does |
|---|---|---|
| `commit_window` | `200` | The latest non-merge commits the "explain why" measure reads. |
| `proposed_adr_days` | `30` | A proposed ADR older than this needs attention. |
| `trend_updates` | `12` | The updates a trend line shows. |
| `max_listed` | `50` | The items a list shows before the rest fold away. |
| `max_message_chars` | `20000` | The part of a commit body that is searched for a reason. |
| `activity_months` | `24` | The months the activity bars show. |
| `history_limit` | `1000000` | The commits whose dates are read. |
| `largest_files` | `10` | The files the Repository page lists by size. |
| `churn_window` | `500` | The latest non-merge commits the hot spots read. |
| `quiet_days` | `365` | Code no commit changed for this long is quiet. |
| `folder_depth` | `2` | The folder levels the change heat map groups by (1 to 6). |
| `heatmap_tiles` | `60` | The tiles a heat map level draws, the largest by lines. |
| `languages` | a built-in table | Maps a file name or suffix to its language, for example `".py" = "Python"`. Names are lower-cased. Setting this table replaces the built-in one whole. |

### `[hosting]` and `[team]`

| Setting | Default | What it does |
|---|---|---|
| `[hosting] gh` | `"gh"` | The GitHub program. |
| `[hosting] az` | `"az"` | The Azure DevOps program. |
| `[hosting] glab` | `"glab"` | The GitLab program. |
| `[hosting] timeout_seconds` | `60` | How long each program run may take (5 to 600). |
| `[hosting] max_output_bytes` | `10000000` | The most output of one program run. |
| `[hosting] max_repositories_per_source` | `1000` | The most repositories read from one source. |
| `[hosting] concurrency` | `4` | A team's signal fetches, and first refreshes, at once (1 to 16). |
| `[team] active_days` | `90` | A source's repositories pushed to within this many days are followed. |
| `[team] highlights_per_digest` | `3` | The highlights each digest carries, which the team page shows on a repository's card (0 to 10). |
| `[team] max_overview_words` | `250` | The most words in a team's overview (50 to 1000). |

## Target settings (`targets/<name>.toml`)

`codetrail target add` writes `repository` and `branch`. The rest are optional.

| Setting | Default | What it does |
|---|---|---|
| `repository` | set by `target add` | The repository's folder. A team's target has `[remote]` and `team` instead; exactly one of `repository` and `remote` is set. |
| `branch` | set by `target add` | The branch Codetrail teaches. |
| `extractors` | all seven | Which extractors run: `"python"`, `"adr"`, `"openapi"`, `"terraform"`, `"swift"`, `"typescript"`, `"github_actions"`. Remove one to stop reading that kind of fact. |
| `[adr] paths` | `["docs/adr/*.md"]` | Where the repository keeps its ADRs. |
| `[openapi] paths` | `["**/openapi.json", "**/openapi.yaml", "**/openapi.yml"]` | Where its OpenAPI files are. |
| `[generation] max_pages_per_update` | `20` | The most pages one update writes. |
| `[generation] concurrency` | `2` | Pages written at once (1 to 8). |
| `[generation] max_turns` | `30` | The most turns one write call takes. |
| `[generation] max_budget_usd_per_call` | `1.0` | The most one call may cost; reserved before it starts. |
| `[generation] max_budget_usd_per_update` | `10.0` | The update's budget at API prices. |
| `[generation] max_tokens_per_update` | `5000000` | The update's token budget, counted for every provider. |
| `[generation] revise_max_changes` | `20` | A page with at most this many changes since it was written is revised, not rewritten. `0` writes every page whole. |
| `[models] plan` | `"claude-opus-5-5"` | The provider and model of the outline call, as `provider:model`. |
| `[models] write` | `"claude-sonnet-5-5"` | The same, for writing pages. |
| `[models] digest` | `"claude-sonnet-5-5"` | The same, for digests. |
| `[models] answer` | `"claude-sonnet-5-5"` | The same, for questions. |
| `[models] grade` | `"claude-sonnet-5-5"` | The same, for grading checks. |
| `[assistant] allow_codex` | `false` | Must be `true` before any call uses Codex. |
| `[metrics] document_globs` | `["README*", "*.md", "*.rst", "*.adoc", "*.txt", "docs/**"]` | Which files count as documents. |
| `[metrics] commit_why_pattern` | a pattern that matches a line starting with "why" | A regular expression that marks a commit that explains why. |
| `[metrics] test_globs` | test folders and `test_*`, `*_test.*`, `*.test.*`, `*.spec.*` files | Which files count as tests. |
| `[metrics] commit_type_pattern` | a Conventional Commit subject | A regular expression with a `(?P<type>…)` group for a commit's type. |

A `[models]` value without a provider (`"claude-sonnet-5-5"`) means Claude Code. With one, it names the provider: `claude_code`, `codex` or `local`, as in `"local:qwen3:14b"`.

## Team settings (`teams/<name>.toml`)

| Setting | Default | What it does |
|---|---|---|
| `[[sources]] service` | | `github`, `azure_devops` or `gitlab`. |
| `[[sources]] source` | | The organization, account, project or group. |
| `[[sources]] follow` | `"all"` | `"all"` follows every active repository; `"chosen"` follows the ones in `chosen`. |
| `[[sources]] clone_over` | `"https"` | `"https"` or `"ssh"`. |
| `[[sources]] left_out` | `[]` | Repositories you unticked. |
| `[[sources]] chosen` | `[]` | The repositories to follow when `follow` is `"chosen"`. |
| `[models] overview` | `"claude-sonnet-5-5"` | The provider and model of the team overview. Codex is refused. |
| `[catch_up] max_budget_usd` | `20.0` | The whole catch-up's spend across its repositories. |
| `[catch_up] concurrency` | `2` | Repositories updated at once (1 to 8). |
| `[catch_up] max_budget_usd_per_call` | `1.0` | The most the overview call may cost. |

The page writes the `sources` lists when you use **Add repositories**. See [Teams](teams.md).

## Troubleshooting

| You see | What it means and what to do |
|---|---|
| `<file>: <key>: Extra inputs are not permitted` | The key isn't a Codetrail setting. Check its spelling and its section against this page. |
| `<file>: <key>: Input should be …` or `… greater than …` | The value is the wrong kind or outside its range. The message names the key. |
| `<file> isn't valid TOML: …` | The file has a syntax error. Fix the line the message names. |
| `'…' isn't a loopback address (127.0.0.1, ::1 or localhost, with no user).` | `[providers.local] base_url` points off your machine. Use a loopback address. |
| `models: Codex can read outside the repository's allowed files …; to use it for this target, set [assistant] allow_codex = true.` | A `[models]` entry uses Codex. Opt in, or choose another provider. See [Assistants](assistants.md). |
| `'…' isn't a provider; use one of claude_code, codex, local.` | The word before the first `:` in a `[models]` value isn't a provider. |
| `No target named '…'. Add it with: codetrail target add … <path>` | There is no `targets/<name>.toml`. Check the name, or add the target. |

More are in [Troubleshooting](troubleshooting.md).

## See also

- [What Codetrail reads](what-codetrail-reads.md), for `[source]` and `[tools]`
- [Keeping the guide up to date](updating.md), for budgets
- [Assistants](assistants.md), for `[providers]` and `[models]`
- [Teams](teams.md), for the team file
