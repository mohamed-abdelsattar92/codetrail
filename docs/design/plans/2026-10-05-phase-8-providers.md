# Phase 8: assistant providers and sign-in — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Codetrail plans, writes, answers and grades with the reader's choice of Claude Code, Codex or a local model, on the reader's own subscription, without ever handling a key or token.

**Architecture:** the `claude` package becomes `assistant`: one `Assistant` protocol, a router that sends each kind of call to the provider and model configured for it, and three adapters. `claude_code` and `codex` run the reader's own programs as child processes with an allowlisted environment and the prompt on stdin; `local` is Codetrail's own read-only tool loop over an OpenAI-compatible endpoint on loopback. Every call returns its `Usage`, which is recorded per target.

**Tech stack:** Python 3.14, anyio processes, httpx, pydantic settings, SQLite migrations, pytest with fake programs and httpx's mock transport.

**Spec:** `docs/design/2026-10-05-codetrail-design.md`, sections 6.9 and 15; ADR 0006 (proposed).

## Global constraints
- The child environment is built from the allowlist in design 15.2 and nothing else; `subscription` passes no key variable; `api_key` passes `ANTHROPIC_API_KEY` (claude_code) or `OPENAI_API_KEY` and `CODEX_API_KEY` (codex) by name only.
- Prompts go to programs on stdin, never in arguments.
- `claude -p` always gets `--tools Read,Grep,Glob` (or `""` for grading), `--allowedTools "Read(./**)" "Grep(./**)" "Glob(./**)"`, `--permission-mode dontAsk`, `--setting-sources ""`, `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`, `--settings` with the guard hook (started by `sys.executable`, with a `timeout`), `--max-turns`, `--max-budget-usd`, `--system-prompt`, `--model`, `--output-format stream-json --verbose`; never `--bare`.
- The guard hook refuses with exit code 2 on any error of its own.
- `codex exec` runs only for targets with `[assistant] allow_codex = true`, and always gets `--sandbox read-only`, `--json`, `--ephemeral`, `--skip-git-repo-check`, `-C <source>`, `--output-schema <file>` for structured calls, and `-c` overrides that switch off MCP servers, notifications and project instructions and inherit no shell environment. Its environment has an empty temporary `HOME`, `SHELL=/bin/sh`, no `USER` or `LOGNAME`, and `CODEX_HOME` made absolute.
- `local` refuses a `base_url` whose parsed host isn't exactly loopback or that has user information; its client has `trust_env=False` and `follow_redirects=False`; its tools read only `source/` through `ToolGuard`; Grep is a plain-text search.
- Provider programs are resolved to absolute paths, with relative `PATH` entries dropped.
- Settings, schema and read-log files come from `mkstemp`, outside `source/`, and are removed after the call.
- Final answers and grading feedback are scanned with gitleaks before they are shown or stored.
- Every `@` in a prompt becomes U+FF20 (all adapters).
- `claude-agent-sdk` is removed; `httpx` becomes a runtime dependency (ADR 0006).
- Limits and prices live in configuration.

## Review focus
- A key variable in Codetrail's own environment must not reach a subscription-mode child: test with `ANTHROPIC_API_KEY` set in the parent.
- A program that prints garbage, stops mid-stream or never ends: parse defensively, time out, and kill the process group.
- A target path with spaces or quotes in it: the hook command and arguments must be quoted correctly.
- A local model that answers without calling the final schema, or calls an unknown tool: one retry with the errors, then a clean `AssistantError`.
- A test that would reach a real provider: the autouse fixture refuses `claude` and `codex` and any non-mock HTTP to the local endpoint.

---

### Task 1: rename `claude` to `assistant`, add `Usage`
**Files:** move `src/codetrail/claude/` to `src/codetrail/assistant/` (`git mv`); rename `Claude`→`Assistant`, `ClaudeError`→`AssistantError`, `FakeClaude`→`FakeAssistant`, `claude_for`→`assistant_for`; update every import in `src/` and `tests/`; `tests/conftest.py`.
**Interfaces (produces):**
```python
@dataclass(frozen=True)
class Usage:
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # the provider's figure, or tokens x prices; None when unpriced
```
Every draft (`PlanDraft`, `PageDraft`, `DigestDraft`, `Verdict`, the final `AnswerChunk`) gains `usage: Usage = field(default_factory=Usage)` and keeps `cost_usd`.
- [ ] Rename with `git mv`, fix imports, run the full suite: green before any behaviour change.
- [ ] Commit `refactor(claude): rename the claude package to assistant`.

### Task 2: the child environment and the program runner
**Files:** `src/codetrail/assistant/environment.py`, `src/codetrail/assistant/runner.py`; tests `tests/unit/test_assistant_environment.py`, `tests/unit/test_assistant_runner.py`.
**Interfaces:**
```python
def child_environment(provider: Literal["claude_code", "codex"], auth: Literal["subscription", "api_key"],
                      environ: Mapping[str, str] = os.environ) -> dict[str, str]: ...
async def run_program(command: Sequence[str], stdin_text: str, cwd: Path, environment: Mapping[str, str],
                      timeout_seconds: float) -> AsyncIterator[str]:  # stdout lines; kills the process group on exit
```
- [ ] Tests: allowlisted names pass; `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, `AWS_SECRET_ACCESS_KEY` and an arbitrary `MY_TOKEN` are dropped in `subscription`; the provider's key names pass in `api_key`; `LC_ALL` passes.
- [ ] Tests: the runner feeds stdin, yields lines, raises `AssistantError` on a non-zero exit with the last stderr line cut to 300 characters, raises on timeout, and leaves no child running (check with a fake program that sleeps).
- [ ] Implement with `anyio.open_process(..., start_new_session=True)`; kill with `os.killpg` in `finally`.
- [ ] Commit `feat(claude): run providers' programs with an allowlisted environment`.

### Task 3: the guard as a hook program
**Files:** `src/codetrail/assistant/guard_hook.py`; test `tests/unit/test_guard_hook.py`.
`<sys.executable> -m codetrail.assistant.guard_hook <source_root> <read_log>` reads the PreToolUse JSON on stdin, decides with `ToolGuard`, appends each allowed Read's relative path to `<read_log>`, and prints the deny output; any error of its own exits with code 2.
- [ ] Tests: allowed Read logged; `/etc/hosts`, `../x`, `Bash`, a malformed payload denied; `StructuredOutput` allowed; an unreadable read log or a crash inside the guard exits 2.
- [ ] Live test (Task 9): with the hook pointed at a missing interpreter, a canary file outside `source/` still can't be read.
- [ ] Commit `feat(claude): run the tool guard as a hook program`.

### Task 4: the `claude_code` adapter
**Files:** `src/codetrail/assistant/claude_code.py`; fake program `tests/fixtures/programs/fake_claude.py`; tests `tests/unit/test_claude_code_adapter.py`.
- [ ] The fake program records its argv, environment and stdin to a JSON file named by `FAKE_PROGRAM_LOG` (passed through the test's `CLAUDE_CONFIG_DIR` folder, since the environment is allowlisted) and replays a stream file.
- [ ] Tests: required flags present, `--bare` absent; the prompt arrives on stdin with `@` neutralized; the hook command quotes paths with spaces; structured output, files read (from the read log), usage, cost and the rate-limit windows are parsed from a recorded stream; `is_error` results and a missing result raise `AssistantError`; `grade` passes `--tools ""`; answers stream `text_delta` chunks.
- [ ] Commit `feat(claude): run Claude Code headless on the reader's sign-in`.

### Task 5: the `codex` adapter
**Files:** `src/codetrail/assistant/codex.py`; fake `tests/fixtures/programs/fake_codex.py`; tests `tests/unit/test_codex_adapter.py`.
- [ ] Tests: refused unless the target sets `allow_codex`; flags and `-c` overrides; the environment (empty `HOME`, `/bin/sh`, no `USER`); stdin; schema file written with `mkstemp` and removed; the final `agent_message` parsed as JSON for structured calls; usage from `turn.completed`; files read collected from `command_execution` events that `cat`/`sed`/`rg` files inside `source/` (best effort, documented); the process is stopped when the summed tokens pass `max_tokens_per_call`; cost from prices when the model is priced.
- [ ] Commit `feat(claude): run Codex on the reader's sign-in, read-only`.

### Task 6: the `local` adapter
**Files:** `src/codetrail/assistant/local.py`, `src/codetrail/assistant/local_tools.py`; tests `tests/unit/test_local_adapter.py`, `tests/unit/test_local_tools.py`.
- [ ] Tools: `read(path, offset, limit)`, `grep(text, path, glob)` (plain text, case-insensitive, at most 200 matching lines), `glob(pattern, path)` (at most 500 paths), each checked by `ToolGuard`, reading at most `max_read_bytes`.
- [ ] Loop: chat completions with `tools`; execute tool calls; stop at `max_turns` or `max_tokens_per_call`; the final message must be JSON matching the schema (checked with the same required-key checks the generation code uses); one retry with the errors.
- [ ] Tests (httpx `MockTransport`): a two-turn tool run; a refused path returned to the model as an error; an unknown tool; malformed JSON then a good retry; no retry left raises; a non-loopback `base_url`, `http://127.0.0.1@evil.example/` and `http://localhost.evil.example/` refused at construction; the client ignores `HTTP_PROXY`; usage summed; cost zero.
- [ ] Commit `feat(claude): answer with a local model through Codetrail's own read-only tools`.

### Task 7: configuration, routing and the providers check
**Files:** `src/codetrail/config.py`, `src/codetrail/assistant/routing.py`, `src/codetrail/assistant/status.py`, `src/codetrail/cli.py`, `src/codetrail/update.py`, `src/codetrail/web/app.py`; tests in `tests/unit/test_config.py`, `tests/unit/test_assistant_routing.py`, `tests/unit/test_provider_status.py`, `tests/e2e/test_providers_command.py`.
- [ ] Config: `[assistant] retry_attempts`; `[providers.claude_code|codex|local]`; `[prices]`; `[models]` values parsed as `provider:model` (no known prefix means `claude_code`); unknown provider refused by name.
- [ ] `build_assistant(source_root, global_config, target_config) -> Assistant` returns a router whose five calls go to the configured adapter and model.
- [ ] `provider_status(name, settings) -> ProviderStatus(installed, signed_in, method, plan, fix)`; uses `claude auth status --json` (keeps `loggedIn`, `authMethod`, `subscriptionType` only), `codex login status`, and `GET {base_url}/models`.
- [ ] `codetrail providers` prints one line per provider; paid actions call `require_ready(kinds)` first.
- [ ] Commit `feat(claude): choose a provider per kind of call, and check sign-in`.

### Task 8: usage records and the output scan
**Files:** `src/codetrail/database/migrations/0004_assistant_usage.sql`, `src/codetrail/assistant/usage.py`, `src/codetrail/generate/run.py`, `src/codetrail/generate/validate.py`, `src/codetrail/bridge.py`, `src/codetrail/learn/routes.py`; tests accordingly.
- [ ] Tables `assistant_calls(id, at, kind, provider, model, input_tokens, cached_input_tokens, output_tokens, cost_usd)` and `plan_usage(provider, window, utilization, resets_at, observed_at)`.
- [ ] Generation, the bridge and grading record every call's usage; the update budget counts recorded costs.
- [ ] Pages, digests and saved answers are scanned with `SecretScanner.scan_text`; a finding fails validation with the rule's name.
- [ ] Commit `feat(generate): record each call's usage, and scan outputs for secrets`.

### Task 9: remove the Agent SDK, update docs, live tests
**Files:** `pyproject.toml`, `uv.lock`, `src/codetrail/assistant/agent_sdk.py` (deleted), `tests/live/`, `README.md`, the design.
- [ ] `uv remove claude-agent-sdk`; delete the old adapter and its tests.
- [ ] Live tests: one tiny structured call and one answer through each installed provider (skipped when the program isn't installed or signed in).
- [ ] Commit `refactor(deps): drop the Claude Agent SDK`.
