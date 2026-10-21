# 0016. Draw diagrams with D2: committed SVGs in the documentation, and D2's WebAssembly build in an isolated worker on the page

- Status: accepted (by the founder, 21 October 2026)
- Date: 2026-10-21
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder asked on 2026-10-21 for an alternative to Mermaid that looks more professional, and chose D2 over React Flow
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 4.4, 7.1, 7.4, 12 and 17.4; brainstorm decision 9; [ADR 0004](0004-server-rendered-page-stack.md), whose Mermaid part this would replace

## Context
- Codetrail draws diagrams in two places: the README's five hand-written diagrams, which GitHub renders, and the page's diagrams, which `web/diagrams.py` writes as Mermaid text from facts and the vendored Mermaid 12.1.0 draws in the browser (ADR 0004). The Repository and Documentation pages' charts are drawn by Codetrail's own code and aren't affected.
- The founder finds Mermaid's diagrams don't look professional. D2 (https://d2lang.com, by Terrastruct, MPL-2.0) is a diagram language with better layout and themes for architecture diagrams, and supports flowcharts, sequence diagrams and the shapes the system diagram uses (design 17.4).
- GitHub renders only Mermaid inside Markdown. Any other tool's diagrams have to be committed as images.
- Facts (checked on 2026-10-21):
  - D2's command-line program is a Go binary, released on GitHub and listed in mise's registry (`aqua:d2lang/d2`).
  - D2.js 0.1.33 (`@terrastruct/d2` on npm, MPL-2.0, tarball integrity `sha512-eK5hyfGIJFolC7sUsiKvWdY9xGFctTe3d+PSijo09IYDso8psztC+A4SammizXtlwYZpnnW0AtDjfBYauceSeA==`) is D2 compiled to WebAssembly. Its build reports D2 `v0.7.0-HEAD`. It ships `d2.wasm` (22 MB) and Go's `wasm_exec.js` (BSD-3, The Go Authors), plus a one-file browser bundle (8 MB).
  - The browser bundle starts its worker from a `blob:` URL and loads the ELK layout with `new Function`. A worker started from a `blob:` URL takes the page's Content-Security-Policy, so the page itself would need `blob:` and `'unsafe-eval'`.
  - D2's default layout engine, dagre, runs in the WebAssembly build through the JavaScript engine's string evaluation, so even without ELK the worker needs `'unsafe-eval'` (measured in headless Chromium: without it, every compile fails with a CSP violation).
  - A dedicated worker loaded from its own same-origin URL takes the policy sent with its script instead of the page's. In headless Chromium, a worker served with `default-src 'none'; script-src <Go's loader's URL> 'unsafe-eval' 'wasm-unsafe-eval'; connect-src <the wasm file's URL>` compiled and rendered D2 while the page kept its current policy unchanged, and a `fetch("/")` from that worker was refused. The first diagram took about 2 seconds (most of it compiling the wasm); each diagram after it took under 0.1 seconds.
  - Labels written as D2 double-quoted strings, with `"`, `\` and `$` escaped, came back unchanged, including `<script>`, `${x}` and `@import`.
  - D2's dagre layout writes edge ids into the JavaScript it evaluates, escaping only backslashes and line breaks (`d2layouts/d2dagrelayout/object_mapper.go`), so a name used as a D2 key could run as code in the worker. Codetrail's D2 keys are generated ids (`n1`, `n2`, …); names appear only as quoted labels.
  - D2.js 0.1.33 is the latest on npm, and its build is D2's master after v0.7.0 (May 2025); D2's latest release is v0.9.0. The page can only move to a newer D2 when D2.js publishes one.

## Decision drivers
1. Security: the page's Content-Security-Policy doesn't loosen, and names from the target repository stay plain text (design 7.4).
2. Diagrams that look professional in the README and on the page.
3. Local by default: no CDN, no hosted service, nothing more for the reader to install than `uv tool install`.
4. One diagram language for the documentation and the page.
5. Standard tools and as little code of Codetrail's own as possible (AGENTS.md, writing code rules 1 and 6).

## Options considered
### Option A: keep Mermaid and restyle it
- Good, because it needs no new dependency, and GitHub keeps rendering the README's diagrams as text.
- Bad, because theme variables change colours and fonts but not Mermaid's layout, which is most of what looks unprofessional; GitHub's own rendering of the README ignores the page's theme.

### Option B: D2, with D2.js's browser bundle on the page
- Good, because the bundle is meant for browsers and needs no code of Codetrail's own beyond calling it.
- Bad, because its `blob:` worker takes the page's policy, so the page would need `'unsafe-eval'` and `blob:` in `script-src`: any script injection would then also have `eval`. That fails driver 1.

### Option C: D2, with the documentation's diagrams committed as SVGs and the page's drawn by D2's WebAssembly build in an isolated worker (chosen)
- Good, because the page's policy stays exactly as it is. The worker that needs `'unsafe-eval'` has no DOM, no network but the one wasm file, and gets only D2 text Codetrail wrote from facts; what it returns is shown as an `<img>`, where an SVG can't run script or follow links, so even a compromised worker can't reach the page or the session.
- Good, because the same language and the same D2 release draw the README and the page, and the reader installs nothing more.
- Bad, because Codetrail vendors 22 MB of WebAssembly (Mermaid is 13 MB) and writes a small worker (about 40 lines) instead of using D2.js's wrapper.
- Bad, because the first diagram on each page load takes about 2 seconds while the browser compiles the wasm.
- Bad, because a diagram shown as an `<img>` can't be searched with the browser's find, and its text can't be selected; the list of what each box links to, under every diagram, stays the accessible text.
- Bad, because the README's diagrams become images: GitHub can't show their text, they follow the reader's system theme rather than GitHub's setting, and they need regenerating (`just diagrams`) when their sources change.

### Option D: D2, rendered on the server by the `d2` binary
- Good, because no `eval` runs in the browser at all.
- Bad, because every reader would have to install a Go binary next to `uv tool install`, and Codetrail would run a parser on text built from the target's names in a process that can read files (D2 has `@import`), so an escaping bug would reach the filesystem.

### Option E: React Flow
- Bad, because it is a React library: the page would need React, a bundler and a layout library (dagre or elkjs), which ADR 0004 rejected as option B, and GitHub can't render it, so the README would still need another tool.

## Decision
Option C. The README's and docs' diagrams are written in D2 in `docs/diagrams/` and committed as SVGs rendered by the pinned `d2` binary. The page's diagrams are written as D2 by `web/diagrams.py` and drawn by D2's WebAssembly build in a dedicated worker with its own narrow policy, and shown as images. It wins on driver 1, where option B fails, and on drivers 2 and 4, where option A falls short, at the cost of a large vendored file and a slower first diagram.

## Consequences
- Easier: one diagram language everywhere; D2's layout and themes; the page's policy no longer needs `'unsafe-inline'` styles for diagrams once Mermaid is gone (other inline styles would have to be checked first).
- Harder: D2.js is young (0.1.x), lags D2's releases and is updated by hand, like Mermaid; the vendored files and the `d2` pin are updated together, with their version and checksums recorded. The worker's policy is a second policy to keep tight, with its own tests, and D2 keys must stay generated ids.
- Revisit if D2's WebAssembly build stops needing `eval`, if a build without dagre's JavaScript appears, or if the first-diagram delay bothers readers.

## Changes required
- [x] `.mise.toml`: the `d2` binary, pinned.
- [x] `justfile`: `just diagrams` renders `docs/diagrams/*.d2` to SVGs marked GENERATED.
- [x] `README.md`: its diagrams as committed SVGs.
- [x] `src/codetrail/web/static/vendor/d2/`: `d2.wasm` and `wasm_exec.js` from D2.js 0.1.33, with `VERSION` and the licences.
- [x] `src/codetrail/web/security.py`: the worker's own policy.
- [x] `web/diagrams.py`, `diagram.js` and a worker: the system diagram in D2.
- [x] `web/diagrams.py`: every diagram kind in D2, then the Mermaid text, `vendor/mermaid.js` and its tests removed.
- [x] `justfile` and CI: a check that the committed SVGs match their sources.
- [x] Design sections 4.4, 7.1, 7.4, 12 and 17.4, brainstorm decision 9 and the README's acknowledgements: D2 instead of Mermaid.
- [x] `docs/security/review-checklist.md`, the Rendering item: D2 labels as double-quoted strings with `\`, `"` and `$` escaped, D2 keys generated, and the worker's policy served only for `/static/js/d2-worker.js`, after the Host check.
- [x] `src/codetrail/web/security.py`: the page's policy without `'unsafe-inline'` styles, which only Mermaid needed (no template, script or Python code writes inline styles).
- [x] ADR 0004: status `superseded by 0016` for its Mermaid part.
- [x] The founder accepted this ADR on 21 October 2026.
