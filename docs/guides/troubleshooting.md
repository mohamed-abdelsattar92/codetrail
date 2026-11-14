# Troubleshooting

Every message you can meet in Codetrail, what it means and how to fix it, grouped by what you were doing. Find your message in the table for the feature, or use your browser's search. A `…` stands for a name, a number or a reason that differs each time.

Command-line messages start with `codetrail:` and exit with code 1. Messages on the page appear in the page's own panels and dialogs.

**Before you start:** nothing. If you haven't set Codetrail up, begin with [Your first guide](first-guide.md).

## Check the basics first

1. Run `codetrail providers` to see whether your assistant is installed and signed in.
2. Run `codetrail files shop` (your target's name) to see whether Codetrail can read the repository. This also runs gitleaks.
3. Run `codetrail update shop --facts-only`, which is free, to see whether the facts refresh.

If all three work, the problem is in the step after them.

## Setting up and reading the repository

See [Your first guide](first-guide.md) and [What Codetrail reads](what-codetrail-reads.md).

| You see | What it means and what to do |
|---|---|
| `'…' isn't a valid target name: use lower-case letters, digits and hyphens, …, at most 63 characters.` | Choose another name for `codetrail target add`. |
| `'teams' is reserved: a team's data lives in a folder of that name.` | Choose another target name. |
| `A target named '…' already exists: …` | The name is taken. Use another, or remove the target first with `codetrail target remove`. |
| `… isn't a git repository (it has no .git).` | Give `codetrail target add` the folder that holds `.git`. |
| `… has no branch '…'.` | The branch isn't in the repository. Add `--branch` with its real name. |
| `'…' isn't a valid branch name.` | Check the name you gave `--branch`. |
| `Codetrail's folder … can't be inside the repository, nor the repository inside it.` | Move the repository, or point `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_STATE_HOME` somewhere that isn't inside it. Codetrail never writes into a repository. |
| `No target named '…'. Add it with: codetrail target add … <path>` | The name has no settings file. Check the spelling, or add the target. |
| `git … failed: …` | git refused. The message ends with what git printed. |
| `… holds a .gitleaksignore, which gitleaks would obey; refusing to scan it.` | Codetrail hides `.gitleaksignore` files before it scans, so this shouldn't appear. If it does, report it. |
| `gitleaks wasn't found (…); Codetrail won't read a repository without it. …` | Install gitleaks and put it on your `PATH`, or set `[tools] gitleaks` in `config.toml` to its absolute path. |
| `gitleaks didn't finish within … seconds ([tools] gitleaks_timeout_seconds); …` | For a large repository, raise `gitleaks_timeout_seconds` under `[tools]`. |
| `gitleaks failed (exit code …); the scan didn't complete. It said: …` | gitleaks itself failed. To run a different gitleaks, set `[tools] gitleaks`. |
| `… is a mise shim, and mise couldn't say which gitleaks it runs here (exit code …). …` | The `gitleaks` on your `PATH` is a mise shim and no mise configuration where you ran Codetrail sets its version. Run Codetrail from a folder whose mise configuration sets gitleaks, run `mise use -g gitleaks`, or set `[tools] gitleaks` to the binary (`mise which gitleaks` prints it). |
| `… is a mise shim, and mise didn't say which gitleaks it runs here within … seconds …` | mise didn't answer in time, often because a mise configuration runs a slow command. Check that folder's mise configuration, run Codetrail from another folder, or set `[tools] gitleaks`. |
| `mise named … as gitleaks here, which isn't one of mise's own installs …; Codetrail won't run it.` | A mise configuration in the folder you ran Codetrail from names a gitleaks outside mise's installs. Run Codetrail from another folder, or set `[tools] gitleaks` to a gitleaks you trust. |
| `The target's .codetrailignore has … lines with a pattern; Codetrail reads at most …` | The repository's ignore file is too long. Ask for it to be shortened, or raise `[source] max_ignore_lines`. |
| `The target's .codetrailignore, line … is … bytes; …` | A line is longer than any path Codetrail reads. Ask for it to be removed or shortened. |
| `The target's .codetrailignore, line …, is too slow to match: …` | A line has too many `**` parts and extra `*`. Ask for it to be split into simpler lines. |
| `The target's .codetrailignore is too slow to match: …` | Matching took longer than `[source] max_ignore_match_seconds`. Ask for the wildcard lines to be simplified, or raise the setting. |
| `The target's … is … bytes at commit …; Codetrail reads at most … ([source] max_ignore_bytes).` | The repository's own ignore file (or another file Codetrail reads whole) is larger than the limit named in brackets. Shorten it, or raise the setting. |
| `too large` beside a file in `codetrail files` | The file is larger than `[source] max_file_bytes`. Raise the setting if the assistant should see it. |
| `unscannable (…)` beside a file in `codetrail files` | gitleaks couldn't read the file's compressed text whole, so it stays hidden. Raise `[source] max_inflated_bytes` for a large archive. |
| `git couldn't fetch the branch's tags into the mirror.` | git failed while copying the branch's tags into Codetrail's private copy. Try the command again. |

## Updating the guide

See [Keeping the guide up to date](updating.md).

| You see | What it means and what to do |
|---|---|
| `Target '…' is already updating; try again when that finishes.` | Another update (from the page or the terminal) holds the target. Wait for it. |
| `Target '…' is being removed.` | `codetrail target remove` is running for it. |
| `There's no terminal to ask in, so nothing was spent. Run again with --yes to go ahead.` | `codetrail update` ran without a terminal, and exits with code 2. Read the estimate and add `--yes`. |
| `The facts were refreshed; the guide wasn't updated.` | You answered no at `Continue? [y/N]`. The facts are current; nothing was spent. |
| `Nothing for the assistant to do in this update.` | No page needs writing. This is not an error. |
| `The update's budget ($…) is smaller than one call's cap ($…), so no call can start; …` | Raise `[generation] max_budget_usd_per_update`, or lower `max_budget_usd_per_call`. |
| `The guide at … has uncommitted edits; commit or discard them before updating.` | Someone edited the guide's own git repository by hand. Commit or discard the edits. |
| `The assistant's outline had no page Codetrail could use: …` | The outline call's answer was unusable. Run the update again. |
| `Outline: …` below the update's summary | One outline entry was refused for the reason shown. The rest were used. |
| `Not rewritten: <page> (…)` | That page failed. Its reason is in brackets. |
| `Skipped: <page> (it failed last time, and nothing in it changed since)` | Run `codetrail update shop --retry-failed` to write it anyway. |
| `“…” waits for the next update: the budget is spent.` | The update's budget ran out. Run another update, or raise the budget. |
| `The server stopped, so the update stopped; the guide wasn't changed.` | You stopped `codetrail serve` during an update. Run it again. |
| `An update that calls an assistant must show its estimate and be confirmed first.` | An update was started without its estimate. This is a bug; report it. |
| An update finished a moment ago; wait a few minutes before the next. | The page waits `[server] update_cooldown_seconds` between updates. Wait, or update from the terminal. |
| An update is already running. | Only one update runs at a time. |
| The estimate expired before it was confirmed, so nothing was spent. | You waited longer than `[server] estimate_ttl_seconds`. Choose **Update the guide** again. |
| The update failed (…). | An unexpected error; the name in brackets is its type. Run the update from the terminal for more. |
| `… was written by a newer Codetrail (schema …, this one knows …).` | The target's database came from a newer Codetrail. Upgrade Codetrail with `uv tool upgrade codetrail-cli`. |

## Assistants

See [Choose assistants and models](assistants.md).

| You see | What it means and what to do |
|---|---|
| ``claude_code isn't ready. Install Claude Code (https://code.claude.com/docs/en/setup), run `claude` once and sign in with /login …`` | Claude Code isn't installed. Install it, run `claude`, and sign in with `/login`. |
| ``claude_code isn't ready. Run `claude` and sign in with /login using your Claude subscription.`` | Claude Code is installed but not signed in. |
| `claude_code isn't ready. Claude Code isn't signed in with a Claude subscription: …` | You signed in with an API (Console) account. Run `/login` again with your Claude account, or set `[providers.claude_code] auth = "api_key"` on purpose. |
| ``claude_code isn't ready. `claude auth status` didn't answer; run `claude` once to finish its setup.`` | Run `claude` once and finish its setup. |
| `codex isn't ready. Install Codex …` or `Run codex login and choose Sign in with ChatGPT.` | Install Codex, run `codex login`, and choose **Sign in with ChatGPT**. |
| `codex isn't ready. Codex is signed in with an API key: …` | Sign in with ChatGPT, or set `[providers.codex] auth = "api_key"` on purpose. |
| `local isn't ready. Start Ollama (ollama serve) or LM Studio's server, or set [providers.local] base_url (…).` | Start the local server and pull a model, or fix `base_url`. |
| `models: Codex can read outside the repository's allowed files …; to use it for this target, set [assistant] allow_codex = true.` | Opt in for this target, knowing what Codex can read, or use another provider. |
| `This target doesn't allow Codex; set [assistant] allow_codex = true to use it.` | The same, at the time of the call. |
| `Grading can't use Codex, which always has its shell; choose another provider for grade.` | Set `[models] grade` to a Claude Code or local model. |
| `The team's overview can't use Codex.` | Set the team's `[models] overview` to a Claude Code or local model. |
| `… isn't installed (it isn't on PATH).` / `… isn't installed or isn't executable.` | The program named in `command` isn't there. Install it or set its absolute path. |
| `The program '…' must be a name on PATH or an absolute path.` | Fix `command` under `[providers.…]`. |
| `CODEX_HOME must be an absolute path, not '…'.` | Set the `CODEX_HOME` environment variable to an absolute path. |
| `… didn't answer within … seconds.` / `The provider's program passed its time limit and was stopped.` | A call ran out of time. Try again, or raise `timeout_seconds` for the provider. |
| `… stopped with exit code …: …` / `… couldn't start (…).` | The assistant's program failed. Run it yourself to see why. |
| `… returned more than … bytes, so it was stopped and its output isn't used.` | The program's output passed `[providers] max_output_bytes`. |
| `Claude Code ended without a result.` / `Claude Code didn't finish the task (…).` | The call failed. Retry; it is retried `[assistant] retry_attempts` times already. |
| `The local model at … couldn't be reached (…).` | The local server stopped or `base_url` is wrong. |
| `The local model answered with HTTP …` / `… reply wasn't JSON.` / `… had no message.` | The local server answered badly. Check its log and model. |
| `The local model passed its token limit (…).` / `… didn't finish within … turns.` | Raise `[providers.local] max_tokens_per_call` or `max_turns`, or use a larger model. |
| `The local model's answer wasn't usable: …` | The model didn't follow the task's format. Try a larger model. |

## Serving and the page

See [Getting around](getting-around.md).

| You see | What it means and what to do |
|---|---|
| The sign-in link doesn't work | It works once, for a short time (`[server] login_code_ttl_seconds`, 60 seconds by default). Stop `codetrail serve` and start it again for a new link. |
| `Target '…' is in use: stop the Codetrail server serving it …, or wait for its update to finish.` | You ran `codetrail target remove` while the target is served or updating. Stop those first. |
| `Can't lock target '…' (…): …` | The file system doesn't allow the locks Codetrail needs, or the settings file can't be read. Check the path in the message. |
| `Couldn't check for new commits just now.` on the home page | The "behind" check couldn't read the branch. Reload later; run `codetrail files shop` to see the underlying error. |

## Reading and checks

See [Reading and learning](reading.md).

| You see | What it means and what to do |
|---|---|
| That page doesn't exist. / That check doesn't exist. | The page or check was removed by an update. Reload the page. |
| Answers are limited to … characters. | Shorten your answer. The limit is in the message. |
| Another answer is being graded. | Wait for the first to finish. |
| Wait a few seconds before the next answer. | Grading has a cooldown, `[learn] grading_cooldown_seconds`. |
| The grade couldn't be read, so nothing was recorded; try again. | The assistant's grade wasn't usable. Choose **Check my answer** again. |
| The feedback was withheld: it contains something that looks like a secret (…). | Codetrail doesn't show feedback that looks like a secret. Choose **Check my answer** again. |

## Search and ask

See [Search and ask](search-and-ask.md).

| You see | What it means and what to do |
|---|---|
| Run codetrail … first. | There are no facts yet. Run `codetrail update <name>` (or `--facts-only`). |
| Questions are limited to … characters. | Shorten your question; the limit is `[bridge] max_question_chars`. |
| Another question is still being answered. | Wait for the first answer to finish. |
| The answer failed (…). | An unexpected error; the name in brackets is its type. Ask again. |
| That answer is gone; ask again. | An unsaved answer is kept for the session only, and the oldest goes first (`[bridge] max_session_answers`). Ask again, and use **Save to guide** to keep one. |
| An update is running; save again when it finishes. | Saving waits for the update to end. |

## Exploring the code

See [Exploring the code](exploring-the-code.md).

| You see | What it means and what to do |
|---|---|
| `No facts yet. Run: codetrail update … --facts-only` | `codetrail metrics` or `codetrail stats` has nothing to read yet. Run the command it names. It exits with code 1. |
| `Documented rationale: no pages yet` | The guide has no pages yet. Run a full `codetrail update`. |
| `…: not available, the commit history couldn't be read` | git couldn't read the history. Run `codetrail update … --facts-only` again. |
| `Releases: not available, the tags couldn't be read` | git couldn't read the tags. |
| No **System** link, or no facts for TypeScript, JavaScript or Astro | A target's settings may list its extractors. Add `"typescript"` and `"github_actions"` to `extractors` in the target's file, then run `codetrail update <name> --facts-only`. |
| `Unresolved references (…): …` or `Warning: …` in the update's summary | An extractor couldn't resolve something, or skipped a file larger than `[extract] max_file_bytes`. It is a note, not a failure. |

## Coding agents

See [Coding agents](coding-agents.md).

| You see | What it means and what to do |
|---|---|
| ``Run `codetrail update …` first: there are no facts to map yet.`` | `codetrail agents` needs facts. Run `codetrail update <name>` or `--facts-only`. |

## Teams

See [Teams](teams.md).

| You see | What it means and what to do |
|---|---|
| `A team named '…' already exists: …` | The name is taken. Use `codetrail team serve <name>` for the existing one. |
| `'…' isn't a valid team name: …, at most 30 characters.` | Choose another team name. |
| `No team named '…'.` | There is no `teams/<name>.toml`. Check `codetrail team list`. |
| `Couldn't list the team's sources: …` | The hosting program failed. Check that `gh`, `az` or `glab` is installed and signed in. |
| `git couldn't fetch …. If it needs your sign-in, run: …` | git can't read the repository without asking. Run the command it prints yourself. |
| `That isn't a clone URL for …: use https on … (https://…/...) or git@…:..., with no user, password or port.` | The repository's address isn't the service's own. Don't edit a team target's `[remote]` by hand. |
| `… has no branch '…'.` | The repository's default branch has changed or is missing. |
| `'…' isn't a valid organization name for azure_devops.` / `… isn't a valid … name for ….` | Fix the name you typed for the source. |
| `Team '…' is being removed.` | `codetrail team remove` is running. |
| ``Team '…' is in use: stop `codetrail team serve` for it, or wait for its sync to finish.`` | Stop `codetrail team serve`, then remove the team. |
| `… is followed by team …: leave it out on the team page (Add repositories), then run codetrail target remove again.` | Untick the repository on **Add repositories**, then remove its target. |
| `The team's budget is spent, so this repository wasn't updated.` | The catch-up reached `[catch_up] max_budget_usd`. Catch up again, or raise the budget. |
| A catch-up is already running. / A catch-up finished a moment ago; wait a few minutes before the next. | Only one catch-up runs at a time, with a cooldown after it. |
| That estimate isn't waiting any more. | The estimate expired or was used. Choose **Catch up** again. |
| Another save is in progress. / The repositories added last are still being refreshed. | Wait a moment and try again. |
| Couldn't read this repository (…). / Couldn't read this repository's guide (…). | The team page couldn't open that repository. The name in brackets is the error type; check its guide with `codetrail serve <target>`. |
| `codetrail team add` / `team serve` shows a service as not installed or not signed in | Install `gh`, `az` or `glab` and sign in, as the page says. |

## Removing a target or team

| You see | What it means and what to do |
|---|---|
| `There's no terminal to ask in, so nothing was removed. Run again with --yes to go ahead.` | Add `--yes`. The command exits with code 2. |
| `Nothing was removed.` | You answered no. |
| `No target named '…'.` / `No team named '…'.` | Check the name. |

## Settings files

See [Settings](settings.md).

| You see | What it means and what to do |
|---|---|
| `<file> isn't valid TOML: …` | Fix the syntax error the message names. |
| `<file>: <key>: Extra inputs are not permitted` | The key isn't a setting. Check its spelling and section. |
| `<file>: <key>: …` | The value is invalid. The message says why. |
| `'…' isn't a loopback address (127.0.0.1, ::1 or localhost, with no user).` | `[providers.local] base_url` must be on this machine. |
| `set exactly one of repository and remote.` | A target file has both or neither. Keep one. |
| `isn't a valid regular expression: …` / `needs a (?P<type>...) group` | Fix a `[metrics]` pattern in the target's file. |

## See also

- [Your first guide](first-guide.md)
- [What Codetrail reads](what-codetrail-reads.md)
- [Keeping the guide up to date](updating.md)
- [Settings](settings.md)
