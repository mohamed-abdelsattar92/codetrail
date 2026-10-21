# 0017. Serve coding agents through a Model Context Protocol tool server over standard input and output, written with the standard library

- Status: proposed
- Date: 2026-10-22
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), in the design session of 2026-10-22 on context for coding agents
- Design: `docs/design/2026-10-05-codetrail-design.md` section 23 (23.5 and 23.7); brainstorm decision 15

## Context
Section 23 gives coding agents a map of the repository and a brief per area. Agents should load the briefs only when a task touches their area, with each brief marked when it may be stale, so most of the savings need a way for the agent to ask Codetrail, not only a file to read. Codetrail can't write into the target repository (AGENTS.md, rule 6), so the way in has to be something the reader registers with their own agent.

Facts (checked on 2026-10-22):
- Claude Code and Codex both start Model Context Protocol (MCP) servers that the reader registers (`claude mcp add`, local scope, kept in the reader's own settings; `codex mcp add`, kept in `~/.codex/config.toml`). Over standard input and output, a server is a child process speaking JSON-RPC 2.0, one message per line.
- Codetrail's tool server needs only a small part of the protocol: `initialize` and the `initialized` notification, `ping`, `tools/list` and `tools/call`, with text results. It needs no resources, prompts, sampling, HTTP transport or authorization.
- The official Python SDK, `mcp` 2.3.0 (2 October 2026), requires anyio, httpx2, jsonschema, mcp-types, opentelemetry-api, pydantic, pyjwt with its crypto extra, python-multipart, sse-starlette, starlette, typing-extensions, typing-inspection and uvicorn. Most of that serves the HTTP transport and its authorization, which Codetrail wouldn't use.
- Codetrail's runtime dependencies are the 14 packages in `pyproject.toml`. None of them speaks MCP.

## Decision drivers
- Security: no network surface, and as little code as possible that Codetrail doesn't use.
- It works with the real Claude Code and Codex the reader has installed.
- Few dependencies, and none outside the stack without reason (AGENTS.md, rule 7).
- Plain, standard code that's easy to follow (AGENTS.md, "Writing code").
- Following the protocol as it changes.

## Options considered
### Option A: the official `mcp` SDK
- Good, because the SDK follows the protocol's changes, and its server API is short.
- Bad, because it brings about a dozen new runtime packages, including JWT and crypto libraries and an HTTP server stack. Codetrail would use none of them, and each one is more code to keep up to date and review.
- Bad, because pinning it pins pydantic, starlette and uvicorn ranges against FastAPI's.

### Option B: a small stdio server written with the standard library
- Good, because it adds no dependency: `json`, `sys` and the protocol's five methods, in roughly 150 lines with tests.
- Good, because it can't listen on a port: the HTTP transport doesn't exist in it.
- Bad, because Codetrail follows the protocol itself. That is mitigated by negotiating the version in `initialize` (answering with the client's version when Codetrail supports it, and its latest otherwise) and by a live test with the installed Claude Code and Codex.

### Option C: no tool server; files only, plus a `codetrail context` command the agent runs
- Good, because it needs no protocol at all.
- Bad, because the agent needs shell access and a permission for the command, Codex's `AGENTS.md` can't import a file, and Claude Code asks before it reads files outside the repository. Loading on demand would depend on each agent's habits.

## Decision
Option B. A tool server over standard input and output, written with the standard library, gives Claude Code and Codex the same four read-only tools. It adds no dependency and no network surface, and it has the least code Codetrail doesn't need. The files of section 23.4 stay available on their own for readers who don't register it.

## Consequences
- Easier: agents load only the briefs a task needs; Codetrail's dependencies are unchanged.
- Harder: Codetrail keeps its protocol handling current. A new protocol version that a supported agent requires means a code change, and `just test-live` catches it.
- Revisit when Codetrail needs more of the protocol (resources, prompts, the HTTP transport) or when the protocol changes faster than the live test can keep up. Then the SDK's dependencies would pay for themselves.

## Changes required
- [ ] Design section 23.5 names this ADR as decided.
- [ ] The `agents` module's tool server, with unit and subprocess protocol tests (section 23.8).
- [ ] `just test-live` starts the tool server from the installed Claude Code and Codex.
- [ ] `docs/security/review-checklist.md` covers the tool server's inputs and outputs.
- [ ] `docs/adr/README.md` lists this ADR.
