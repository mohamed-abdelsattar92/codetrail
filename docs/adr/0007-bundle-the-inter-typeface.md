# 0007. Bundle the Inter typeface with the page

- Status: proposed
- Date: 2026-10-05
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder chose "indigo and Inter" in the design session of 2026-10-05
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 12 and 16.2; ADR 0004 (the page's stack)

## Context
The founder asked for a redesign of the page in the manner of Stripe's documentation, and chose a visual direction built on the Inter typeface. The page is local by default: it loads nothing from the internet, and its Content-Security-Policy is `default-src 'self'` (design section 7.4), so a font can only come from Codetrail itself.

Facts (checked on 2026-10-05):
- Inter is released under the SIL Open Font License 1.1, which allows bundling and redistribution with software as long as the licence travels with the fonts (https://github.com/rsms/inter, https://openfontlicense.org).
- Inter ships variable WOFF2 files, one upright and one italic, each covering every weight. Together they are under 1 MB. Every browser Codetrail supports reads WOFF2 and variable fonts.
- The system font stack (San Francisco on macOS, Segoe UI on Windows, Cantarell or DejaVu on Linux) needs nothing, but looks different on each system, and on Linux noticeably plainer.

## Decision drivers
- The page looks the same, and as intended, on every reader's machine.
- Nothing is loaded from outside Codetrail (non-negotiable 4) and the security policy doesn't change.
- No build step and no new runtime dependency.
- Licensing is clear.

## Options considered
### Option A: bundle Inter's variable WOFF2 files under `static/vendor/inter/`
- Good, because the page looks the same everywhere, and Inter is made for screens and small sizes.
- Good, because it is static files served by the existing static route; the security policy's `default-src 'self'` already allows them.
- Bad, because the package grows by under 1 MB, and the files must be updated by hand (rarely: Inter's releases are years apart).

### Option B: the system font stack
- Good, because nothing is added.
- Bad, because the page looks different on each system, and the chosen design was drawn with Inter.

### Option C: load Inter from Google Fonts or another CDN
- Bad, because the page would call an outside service on every load, which non-negotiable 4 rules out, and the security policy would have to allow it.

## Decision
Option A. Codetrail bundles Inter's upright and italic variable WOFF2 files, with the OFL text, in `src/codetrail/web/static/vendor/inter/`, declared with `@font-face` and `font-display: swap`, falling back to the system font stack. The version and the files' SHA-256 sums are recorded in `static/vendor/inter/VERSION`, as Mermaid's are.

## Consequences
- The page uses the typeface the design was drawn with, on every system.
- The package grows by under 1 MB.
- Updating Inter is a manual step: replace the files, the licence if it changed, and `VERSION`.
- Revisit if the font's licence changes, or if the design moves to another typeface.

## Changes required
- [x] Design: sections 12 and 16.2 (done in the design change that proposes this ADR).
- [ ] Code: the font files, licence and `VERSION` in `static/vendor/inter/`; `@font-face` in `static/css/tokens.css` (Phase 11).
- [ ] README: credit Inter and its licence (Phase 11).
