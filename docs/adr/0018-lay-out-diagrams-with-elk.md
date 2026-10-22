# 0018. Lay out the page's diagrams with ELK, and vendor D2's ELK build

- Status: proposed
- Date: 2026-10-22
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder compared Mermaid, D2 with dagre, D2 with ELK and React Flow on the same graphs on 2026-10-22 and chose D2 with ELK
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 4.4, 7.4 and 12; [ADR 0016](0016-draw-diagrams-with-d2.md), whose page layout (dagre) this replaces

## Context
- Since ADR 0016 the page draws every diagram with D2's WebAssembly build in a worker of its own, with D2's default layout, dagre. The README's diagrams are rendered by the `d2` binary with ELK (the Eclipse Layout Kernel).
- The founder compared the four renderings of two graphs: the small example repository's system diagram, and Codetrail's own imports between its 19 top-level packages (78 arrows). D2 with ELK drew right-angled, evenly spaced lines on both; dagre drew loose curves that cross often on the dense graph. The founder chose D2 with ELK.
- Facts (checked on 2026-10-22):
  - D2.js 0.1.33 ships `elk.js` (3.6 MB): elkjs, the JavaScript build of ELK, under the Eclipse Public License 2.0, as D2 adapted it: its in-process worker is exposed (`__elkWorkerClass`, `__elkQvd`), elkjs's `self.onmessage` hook is removed, and D2's synchronous wrapper `elkLayoutSync` is added at the end. sha256 `a297b92da4a257b95c129cc1d8cfb379e2c89bcd4442af90ac06fdd2b1e220b4`. The licence text is elkjs's `LICENSE.md`.
  - D2.js loads `elk.js` and a `setup.js` by fetching them and running them with `new Function`. `setup.js` declares `var setTimeout = function (f) { f() }`, harmless inside `new Function`'s scope but a replacement of the worker's `setTimeout` if imported directly; its only other effect is creating an `ELK` instance, which makes `elkLayoutSync` usable.
  - In headless Chromium, a worker that imports `elk.js` with `importScripts`, creates one `ELK` instance and compiles with `layout: "elk"` drew every diagram kind, with hostile names, and every label came back as written. The page's first drawing took about 1.65 s, against about 1.3 s with dagre.
  - D2's ELK layout, like dagre, evaluates JavaScript built from strings: without `'unsafe-eval'` in the worker's policy every compile fails. So the worker keeps `'unsafe-eval'`, and D2 keys must stay generated ids (ADR 0016). D2 v0.7.0's ELK layout passes the graph to that evaluated script as JSON.
  - Without `worker-src`, a worker's nested workers fall back to `script-src`, so the worker's policy sets `worker-src 'none'`.

## Decision drivers
1. Security: the page's policy doesn't change, and the worker's widens no more than one script.
2. The clearest drawing, the same on the page as in the README.
3. Local by default, and no code of Codetrail's own beyond what the layout needs.

## Options considered
### Option A: keep dagre on the page
- Good, because nothing more is vendored and the first drawing stays at about 1.3 s.
- Bad, because the page and the README look different, and dense diagrams are harder to read.

### Option B: ELK, imported into the worker by `importScripts`, with one `new ELK()` (chosen)
- Good, because the worker's policy only adds `elk.js` to `script-src`; `connect-src` stays the wasm file alone.
- Good, because `setup.js` isn't loaded, so nothing replaces the worker's `setTimeout`.
- Bad, because it vendors 3.6 MB more under a third licence (EPL-2.0), and the first drawing takes about 0.35 s longer.

### Option C: ELK loaded as D2.js does, by `fetch` and `new Function`
- Good, because it matches D2.js exactly.
- Bad, because `connect-src` would have to allow `elk.js` and `setup.js` too, and the worker would run fetched text as code by design.

## Decision
Option B. The worker imports `elk.js`, creates an `ELK` instance and compiles every diagram with `layout: "elk"`; its policy allows `elk.js` as a second script and no nested workers (`worker-src 'none'`). `elk.js` is vendored next to `d2.wasm` with its sha256 in `VERSION` and its licence in `LICENSE-elk`.

## Consequences
- Easier: the page and the README draw diagrams alike, with ELK's tidier lines.
- Harder: one more vendored file, updated with D2.js; EPL-2.0 joins MPL-2.0 and Go's BSD licence in the vendor folder, which a binary distribution must keep alongside the file.
- Revisit if D2.js ships ELK differently, if the first drawing gets slow on large pages, or if a layout without `eval` appears.

## Changes required
- [x] `src/codetrail/web/static/vendor/d2/`: `elk.js`, `LICENSE-elk`, and `VERSION` with its sha256.
- [x] `src/codetrail/web/static/js/d2-worker.js`: import `elk.js`, create an `ELK` instance, compile with `layout: "elk"`.
- [x] `src/codetrail/web/security.py`: `elk.js` in the worker's `script-src`.
- [x] Design sections 4.4, 7.4 and 12, the security checklist's Rendering item, the README's acknowledgements and the changelog.
- [ ] ADR 0016: status line notes its page layout superseded by 0018, when the founder accepts this ADR.
