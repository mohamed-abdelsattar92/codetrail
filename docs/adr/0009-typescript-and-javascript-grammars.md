# 0009. Parse TypeScript, JavaScript and Astro with tree-sitter's TypeScript and JavaScript grammars

- Status: proposed
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder approved the design in the session of 2026-10-05
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 5.2, 12, 13 and 17.1; brainstorm decision 9 (the stack)

## Context
Codetrail extracts facts from Python, Swift, Terraform, OpenAPI and ADRs. Repositories written partly or wholly in TypeScript, JavaScript or Astro get no facts for that code, so no diagrams and no grounding for their pages, and the system diagram (section 17) can't place their parts. The founder asked for a `typescript` extractor reading modules, imports, packages, Astro routes and Cloudflare Workers.

Facts (checked on 2026-10-05):
- tree-sitter is already the stack's parser (decision 9): `tree-sitter` with the Python, HCL and Swift grammars.
- `tree-sitter-typescript` 0.23.2 (MIT) ships the TypeScript and TSX grammars as Python wheels; `tree-sitter-javascript` 0.25.0 (MIT) ships the JavaScript grammar (https://pypi.org/project/tree-sitter-typescript/, https://pypi.org/project/tree-sitter-javascript/). Both are maintained by the tree-sitter organisation and tolerate syntax errors, as the other grammars do.
- Astro files are TypeScript in a `---` fenced block and in `<script>` elements, around an HTML template; the imports Codetrail needs are in those blocks.

## Decision drivers
- One parser family for every language, as decision 9 chose.
- Tolerance of files that don't parse, so one bad file never stops an update.
- No code from the target is ever run.
- Small, well-maintained dependencies with permissive licences.

## Options considered
### Option A: tree-sitter's TypeScript, TSX and JavaScript grammars
- Good, because they match how every other extractor parses, tolerate errors, and run no code.
- Good, because TypeScript's grammar parses the code blocks of Astro files, so Astro needs no grammar of its own.
- Bad, because they are two more wheels to keep up to date.

### Option B: run the TypeScript compiler or esbuild through Node
- Bad, because Codetrail would need Node at runtime, and would run a JavaScript toolchain over the target's code.

### Option C: regular expressions over import lines
- Bad, because comments, strings, template literals and multi-line imports make them wrong in ways a test suite can't fully cover.

## Decision
Option A. `tree-sitter-typescript` and `tree-sitter-javascript` become runtime dependencies, pinned in `pyproject.toml` and `uv.lock`, used only by the `typescript` extractor.

## Consequences
- TypeScript, JavaScript and Astro code gets modules, imports, packages, routes and Workers as facts.
- Two more runtime wheels; Dependabot keeps them current.
- Revisit if the grammars stop being maintained, or if Astro's own grammar becomes necessary for template-level facts.

## Changes required
- [x] Design: sections 5.2, 12, 13 and 17 (done in the design change that proposes this ADR).
- [ ] `pyproject.toml` and `uv.lock`: the two grammars (Phase 12).
- [ ] Code: the `typescript` extractor (Phase 12).
