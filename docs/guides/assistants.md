# Choose assistants and models

Codetrail asks an assistant to plan, write and answer. This page shows how to check which assistants are ready, how to pick the provider and model for each kind of call, and how to keep the cost where you expect it.

**Before you start:** [write your first guide](first-guide.md), or at least have Codetrail installed. You don't need a target to check the providers.

## What an assistant does here

Codetrail makes five kinds of call. Each one names a provider and a model.

| Kind | What it does |
|---|---|
| `plan` | Plans the guide's outline. |
| `write` | Writes or revises one page. |
| `digest` | Writes the digest of what changed. |
| `answer` | Answers a question you ask on the page. |
| `grade` | Grades your answer to a check. |

Three providers can make these calls:

| Provider | What runs | Default sign-in |
|---|---|---|
| `claude_code` | Your own `claude` program (Claude Code). | Your Claude subscription. |
| `codex` | Your own `codex` program (Codex). | Your ChatGPT subscription. Off unless a target opts in. |
| `local` | A model on your machine, through Ollama or LM Studio. | None needed. |

Codetrail runs your own programs on your own sign-in. It never reads, stores or passes a key or a token. Each program starts with a short allowlist of environment variables, and nothing else from Codetrail's environment reaches it.

## Check which assistants are ready

1. Run:
   ```bash
   codetrail providers
   ```
2. Read one line per provider. This is the output on a machine where Claude Code is signed in, Codex isn't installed and no local model server is running:
   ```
   claude_code  ready      Claude subscription (max) · /usr/local/bin/claude
   codex        not ready  
                Install Codex (`npm install -g @openai/codex`), then run `codex login` and choose Sign in with ChatGPT.
                Note: Codex reads outside the repository's allowed files (design section 15.5). Its isolation from your Codex settings is tested against a stand-in, and checked live only where Codex is installed (`just test-live`).
   local        not ready  http://127.0.0.1:9/v1
                Start Ollama (`ollama serve`) or LM Studio's server, or set [providers.local] base_url (http://127.0.0.1:9/v1).
   ```
   The address on the `local` line is the `base_url` Codetrail tried. By default it is `http://127.0.0.1:11434/v1`.

What each part means:

- **ready** or **not ready** tells you whether the provider can make a call now.
- After it comes how the provider is signed in (for example `Claude subscription (max)`) and the full path of its program. For `local` it is the address, and a ready model server also lists its models on a `models:` line.
- An indented line under a provider says how to fix it, or adds a note. Codex always carries the note about reading outside the repository (see [Codex](#use-codex-for-a-repository)).

`codetrail providers` calls no model and costs nothing.

## Use Claude Code (the default)

Claude Code is the default for every kind of call. The models are `claude-opus-5-5` for `plan` and `claude-sonnet-5-5` for the rest.

1. Install Claude Code from <https://code.claude.com/docs/en/setup>.
2. Run `claude` once and sign in with `/login`, using your Claude subscription.
3. Run `codetrail providers` and check that `claude_code` says `ready`.

With the default `auth = "subscription"`, Codetrail passes no key to `claude`, so `claude` uses its own saved sign-in and your subscription pays. If `claude` is signed in with an API account instead, Codetrail refuses to run and tells you to sign in with your Claude account or to change the setting.

### Bill the API instead

Use this only if you want API billing. Add it to `~/.config/codetrail/config.toml`:

```toml
[providers.claude_code]
auth = "api_key"
```

Then Codetrail lets the variable `ANTHROPIC_API_KEY` from your environment through to `claude`, by name. Codetrail doesn't read the value, log it or store it. You are billed by the API, not by your subscription.

## Use Codex for a repository

Codex is off unless a repository opts in. Codex's sandbox stops writes, not reads, and its tools are shell commands. So Codex can read anything you can read, including files Codetrail hides from the other providers (through Codetrail's own data folder) and your own keys. It can run the repository's code inside its sandbox. Whatever it reads goes to OpenAI before Codetrail's secret scan sees it. Codetrail turns off Codex's web search and your Codex settings, and gives it an empty home folder, but it can't confine Codex the way it confines the other two. Use Codex only for repositories where that is acceptable.

Calls that run with no tools at all (grading, and a team's overview) never use Codex.

1. Install Codex and sign in:
   ```bash
   npm install -g @openai/codex
   codex login
   ```
   Choose **Sign in with ChatGPT**.
2. Open the repository's settings file, `~/.config/codetrail/targets/<name>.toml`, and add:
   ```toml
   [assistant]
   allow_codex = true

   [models]
   write = "codex:gpt-5.5-codex"
   ```
3. Run `codetrail providers` and check that `codex` says `ready`.

If a target names a Codex model without `allow_codex = true`, Codetrail refuses to load the target and says that Codex can read outside the repository's allowed files.

A repository followed by a team is a target too, named `<team>-<repository>`. Its settings file is `~/.config/codetrail/targets/acme-web-shop.toml`. Codex stays off for it until you add the same lines to that file.

If Codex is signed in with an API key and you haven't set `auth = "api_key"` under `[providers.codex]`, Codetrail refuses to run and says so.

## Use a local model

A local model answers on your machine. It costs nothing, and the repository's text never leaves your computer. Its answers are usually weaker than a hosted model's, so it fits questions (`answer`) better than writing pages.

1. Start Ollama (`ollama serve`) or LM Studio's server.
2. Run `codetrail providers`. When `local` says `ready`, the line under it lists the models the server offers.
3. In the repository's settings file, give a kind of call a model with the `local:` prefix:
   ```toml
   [models]
   answer = "local:qwen3:14b"
   ```
   The model name may contain colons. Only the first colon after `local` separates the provider.

For LM Studio, or another port, set the address in `~/.config/codetrail/config.toml`:

```toml
[providers.local]
base_url = "http://127.0.0.1:1234/v1"
```

Codetrail accepts only an address on your own machine (`127.0.0.1`, `::1` or `localhost`, with no user name). Prompts carry the repository's text, so they go nowhere else.

## Pick a model for each kind of call

Each repository chooses its models in its own settings file, `~/.config/codetrail/targets/<name>.toml`, as `provider:model`. A value with no provider prefix is a Claude Code model.

```toml
[models]
plan = "claude_code:claude-opus-5-5"
write = "claude_code:claude-sonnet-5-5"
digest = "claude_code:claude-sonnet-5-5"
answer = "local:qwen3:14b"
grade = "claude_code:claude-sonnet-5-5"
```

A team has one more call, the overview, set in the team's own file. See [Teams](teams.md#settings).

Run `codetrail update <name>` to see the effect: the estimate names the provider and model of every call.

## Set prices and budgets

Prices are API list prices in USD per million tokens. Codetrail uses them for estimates and budgets only. They change, so keep them in your configuration. Codetrail knows two models:

| Model | Input | Output | Cached input |
|---|---|---|---|
| `claude-opus-5-5` | 4.0 | 20.0 | 0.4 |
| `claude-sonnet-5-5` | 2.0 | 10.0 | 0.2 |

For any other model, add a price in `~/.config/codetrail/config.toml`. The values below are placeholders; copy the current ones from the provider's pricing page.

```toml
[prices."gpt-5.5-codex"]
input = 1.25
output = 10.0
```

A call whose model has no price has no dollar figure, only tokens. A local model costs nothing. An update still stops at its token limit, which counts every provider.

Budgets stop work before it passes what you decided:

| Setting | File | Default | Stops |
|---|---|---|---|
| `[generation] max_budget_usd_per_update` | repository | 10.0 | One update, in dollars. |
| `[generation] max_tokens_per_update` | repository | 5,000,000 | One update, in tokens, for every provider. |
| `[generation] max_budget_usd_per_call` | repository | 1.0 | One page call. |
| `[bridge] max_budget_usd` | global | 1.0 | One question. |
| `[learn] max_budget_usd` | global | 0.25 | One graded check. |
| `[catch_up] max_budget_usd` | team | 20.0 | A whole team catch-up. |

Every paid call reserves its own cap before it starts, so no call starts past a budget.

## Settings

Global settings go in `~/.config/codetrail/config.toml`. Per-repository settings go in `~/.config/codetrail/targets/<name>.toml`.

| Setting | Default | What it does |
|---|---|---|
| `[models] plan` (repository) | `claude-opus-5-5` | Provider and model of the planning call. |
| `[models] write` (repository) | `claude-sonnet-5-5` | Provider and model of the page-writing call. |
| `[models] digest` (repository) | `claude-sonnet-5-5` | Provider and model of the digest call. |
| `[models] answer` (repository) | `claude-sonnet-5-5` | Provider and model of the answer to a question. |
| `[models] grade` (repository) | `claude-sonnet-5-5` | Provider and model of the grade of a check. |
| `[assistant] allow_codex` (repository) | `false` | Lets the repository use Codex. |
| `[providers.claude_code] command` | `claude` | The program to run. |
| `[providers.claude_code] auth` | `subscription` | `subscription`, or `api_key` to bill the API. |
| `[providers.claude_code] timeout_seconds` | 900 | Time limit of one call. |
| `[providers.codex] command` | `codex` | The program to run. |
| `[providers.codex] auth` | `subscription` | `subscription`, or `api_key`. |
| `[providers.codex] max_tokens_per_call` | 400,000 | Stops a Codex call past this many tokens. |
| `[providers.codex] timeout_seconds` | 900 | Time limit of one call. |
| `[providers.local] base_url` | `http://127.0.0.1:11434/v1` | The model server's address, on this machine only. |
| `[providers.local] max_turns` | 30 | Turns of one call. |
| `[providers.local] max_tokens_per_call` | 200,000 | Stops a call past this many tokens. |
| `[providers.local] timeout_seconds` | 900 | Time limit of one call. |
| `[providers.local] max_read_bytes` | 200,000 | Most the model reads from one file per call. |
| `[providers] max_output_bytes` | 50,000,000 | Stops a program that writes more than this. |
| `[assistant] retry_attempts` | 2 | How many times Codetrail retries a call that failed without using anything. |
| `[prices."<model>"]` | two Claude models | `input`, `output` and optional `cached_input`, USD per million tokens. |
| `[estimates] history_size` | 20 | Recent calls whose median replaces the starting guess. |

## Costs

- `codetrail providers` is free.
- Every paid action shows its estimate first: `codetrail update`, the **Update the guide** dialog, the **Ask** button and **Check my answer**. After the work, the actual tokens and cost appear beside the estimate.
- Estimates start from sensible guesses (marked "a starting guess"), then use the median of your own last calls of the same kind, provider and model.
- On a subscription you pay nothing extra, and the dollars shown are what the same work would cost through the API. The real cost is your plan's usage. The estimate says how much of your plan's usage windows you have used, when Claude Code reports it. Codex doesn't report this, so the page links to its usage page.
- A local model costs nothing.

## Troubleshooting

| Message | Meaning and fix |
|---|---|
| `Install Claude Code (https://code.claude.com/docs/en/setup), run claude once and sign in with /login using your Claude subscription.` | `claude` isn't found. Install it, or set `command` under `[providers.claude_code]` to its path. |
| `Run claude and sign in with /login using your Claude subscription.` | `claude` is installed but signed out. |
| `Claude Code isn't signed in with a Claude subscription: run claude and /login with your Claude account, or set [providers.claude_code] auth = "api_key".` | `claude` bills an API account but your setting says subscription. Sign in with your Claude account, or change `auth` on purpose. |
| `` `claude auth status` didn't answer; run `claude` once to finish its setup. `` | Open `claude` once so it finishes its setup. |
| `Install Codex (npm install -g @openai/codex), then run codex login and choose Sign in with ChatGPT.` | `codex` isn't found. |
| `Codex is signed in with an API key: run codex login and choose Sign in with ChatGPT, or set [providers.codex] auth = "api_key".` | Same mismatch as for Claude Code. |
| `models: Codex can read outside the repository's allowed files (design section 15.5); to use it for this target, set [assistant] allow_codex = true.` | A target names a Codex model without opting in. Add `allow_codex = true` only if you accept what Codex can read. |
| `Start Ollama (ollama serve) or LM Studio's server, or set [providers.local] base_url (...)` | Nothing answers at the address. Start the server, or fix `base_url`. |
| `... isn't a loopback address (127.0.0.1, ::1 or localhost, with no user).` | `base_url` points off your machine. Use an address on this machine. |
| `'<name>' isn't a provider; use one of claude_code, codex, local.` | A `[models]` value has a prefix Codetrail doesn't know. |
| `<provider> isn't ready.` followed by a fix | A paid action was refused because a provider it needs isn't ready. Run `codetrail providers` and follow the fix. |

## See also

- [Your first guide](first-guide.md) and [Update the guide](updating.md): where estimates appear.
- [Teams](teams.md): the team's overview model and budget.
- [Settings](settings.md): every setting in one place.
- [Using Codetrail with your coding agent](coding-agents.md): a different use of Claude Code and Codex, which costs nothing.
