# 0006. Run the reader's own assistant programs (Claude Code, Codex) or a local model, on their subscription

- Status: proposed
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder approved the design in conversation on 2026-10-05
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 2, 6.9, 7.3, 7.4, 9, 10, 11, 12, 13 and 15; brainstorm decision 9 (the Claude Agent SDK); AGENTS.md non-negotiables 4 and 5

## Context
Codetrail calls Claude through the Claude Agent SDK for Python. The founder wants it open to other assistants (Codex, open-source models on the reader's machine), wants each reader's existing subscription used rather than API keys, and wants no paid action to start without an estimate of its cost.

Facts that shape the choice (checked on 2026-10-05):
- Anthropic's Agent SDK overview: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK." Its legal and compliance page asks products to use API keys, and states that this doesn't prevent an end user from signing in to the unmodified Claude Code program with their own subscription (https://code.claude.com/docs/en/agent-sdk/overview, https://code.claude.com/docs/en/legal-and-compliance).
- `claude -p` uses the subscription sign-in when no key is in its environment; an `ANTHROPIC_API_KEY` in the environment takes precedence (https://code.claude.com/docs/en/authentication). It supports `--tools`, `--json-schema`, `--max-turns`, `--max-budget-usd`, `--settings` (for the PreToolUse guard) and `--output-format stream-json`, whose events report tokens, an estimated cost and plan usage windows (verified by a probe on 2026-10-05).
- `codex exec` reuses the reader's `codex login` (ChatGPT) sign-in, runs in a read-only sandbox, takes `--output-schema` and reports tokens; it has no turn or cost limit, and its tools are shell commands (https://learn.chatgpt.com/docs/non-interactive-mode.md). OpenAI recommends API keys for automation and states no rule against a reader's own scripts using their sign-in.
- Ollama and LM Studio serve OpenAI-compatible chat with tool calls on the reader's machine; small models are unreliable at tool calls and schemas (https://docs.ollama.com/capabilities/tool-calling).
- Gemini CLI's terms forbid using its Google sign-in from third-party tools, so it is not included.

## Decision drivers
- Respect each provider's terms.
- Codetrail never handles keys or tokens.
- Read-only access and the secret filter stay enforced, by Codetrail where it can.
- One interface, with vendor code only in adapters (non-negotiable 5).
- Costs are never a surprise.

## Options considered
### Option A: run the reader's own programs, plus Codetrail's own loop for local models
- Good, because it is the use the providers allow: the reader's own unmodified `claude` or `codex`, signed in by the reader, for their own work. Codetrail never sees credentials.
- Good, because each adapter is small; Codetrail keeps its guard for `claude_code` and `local`, and its output scan for all three.
- Bad, because Codex can read and run anything the reader can, including the target's excluded files through Codetrail's data folder; so it is off unless a target opts in, runs with an empty `HOME` and the reader's Codex configuration switched off, has its outputs scanned, and is documented.
- Good, because a probe showed Claude Code's own permission rules (reads allowed only under the working folder, `dontAsk`) confine reads even when the guard hook fails, so `claude_code` has two independent layers.
- Bad, because the programs' output formats can change; the adapters parse them defensively and live tests catch changes.

### Option B: keep the Agent SDK with the subscription sign-in, and add the others beside it
- Good, because the Claude adapter exists.
- Bad, because it is the use Anthropic's note names: an Agent SDK agent offering claude.ai sign-in.

### Option C: one multi-provider tool (for example opencode, or `codex --oss`) for everything
- Good, because there is one integration.
- Bad, because Codetrail's read-only and secret guarantees would rest on a third party's sandbox, and sign-in would still differ per provider.

## Decision
Option A. Codetrail drops the Claude Agent SDK and adds httpx as a runtime dependency (the `local` provider's client). Its `assistant` interface has three adapters: `claude_code` (the reader's `claude -p`), `codex` (the reader's `codex exec`) and `local` (Codetrail's read-only tool loop against an OpenAI-compatible endpoint on loopback). Each provider uses the reader's subscription by default: the programs get an allowlisted environment with no keys. An API key is used only when the reader sets `auth = "api_key"`, and Codetrail then passes the key variable through by name without reading it. Every paid action is preceded by an estimate (design section 15.4).

Codetrail is meant for each person's own use of their own subscription. Anyone who distributes it as a product to others should confirm the terms with Anthropic and OpenAI first.

## Consequences
- Readers choose a provider per kind of call, including free local models.
- The `claude-agent-sdk` dependency is removed; the programs are installed by the reader, like git and gitleaks.
- The adapters depend on the programs' command-line flags and output formats; a change there breaks an adapter until it is updated.
- Codex runs with weaker confinement than the other two, so a target must opt in to it (`[assistant] allow_codex = true`).
- Revisit if Anthropic or OpenAI change their terms for personal tools, if Claude Code or Codex add a supported way for tools to use a subscription, or if a provider adds read confinement.

## Changes required
- [x] Design: section 15, and sections 2, 6.9, 7.3, 7.4, 9, 10, 11, 12, 13 and 14.
- [x] AGENTS.md: non-negotiable 4 ("Claude ... is the only service") and 5 ("one real adapter") name the configured assistant providers.
- [x] Code: the `assistant` package with the three adapters, the allowlisted environment, `codetrail providers`, usage records, output scanning (Phase 8).
- [ ] Code: estimates and their gates (Phase 9).
- [x] Remove `claude-agent-sdk`, and add `httpx` as a runtime dependency, in `pyproject.toml` and `uv.lock` (Phase 8).
- [ ] README and the getting-started guide describe providers, sign-in and estimates (Phase 10).
