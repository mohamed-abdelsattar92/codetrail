# 0014. Bundle Noto Sans Arabic for Arabic text in the page

- Status: proposed
- Date: 2026-10-09
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder chose on 2026-10-09 to add the Arabic interface with the recommended typeface
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 7.2, 12 and 16.2; [ADR 0007](0007-bundle-the-inter-typeface.md) (Inter), whose reasons this follows

## Context
- The page uses Inter, bundled with Codetrail (ADR 0007), and loads nothing from outside it: its Content-Security-Policy is `default-src 'self'` (design section 7.4).
- Inter has no Arabic letters. Arabic text in the page (the Arabic interface, and answers and feedback in Arabic, which the bridge gives in any interface language) falls back to whatever font the system has: Geeza Pro on macOS, Segoe UI on Windows, often DejaVu Sans or nothing designed for it on Linux. The page then looks different on every machine, and next to Inter, unlike anything the design was drawn with.
- Facts (checked on 2026-10-09):
  - Noto Sans Arabic is released by Google's Noto project under the SIL Open Font License 1.1, which allows bundling with software as long as the licence travels with the font (https://github.com/notofonts/arabic, https://openfontlicense.org).
  - Its release 2.013 (`NotoSansArabic-v2.013.zip`, SHA-256 `1301aceaea84c501cf2e6dcfb3182e2328c8eae5725817fcb239672bda7154f1`) holds an unhinted, weight-only variable font, `NotoSansArabic/unhinted/slim-variable-ttf/NotoSansArabic[wght].ttf` (239 KB, weights 400 to 700, which covers the 400, 500 and 600 the design uses). It ships no WOFF2.
  - fontTools' `ttLib.woff2 compress` turns that file into a WOFF2 of 92 KB. WOFF2 is a lossless container: the glyphs, metrics and shaping tables are unchanged.
  - Noto Sans Arabic is a humanist sans drawn to sit beside Latin sans faces of Inter's kind, and it has Western digits, so numbers keep Inter's figures (the founder chose Western digits).
- A `unicode-range` on `@font-face` makes the browser load a face only when the page shows a character in that range, so English pages load nothing more.

## Decision drivers
- Arabic text looks the same, and as intended, on every reader's machine.
- Nothing is loaded from outside Codetrail (non-negotiable 4) and the security policy doesn't change.
- No build step and no runtime dependency; the package grows little.
- Licensing is clear.

## Options considered
### Option A: bundle Noto Sans Arabic's variable font as WOFF2 under `static/vendor/noto-sans-arabic/`, used for the Arabic ranges only (chosen)
- Good, because Arabic looks the same everywhere, in a face made to pair with Latin sans text.
- Good, because the file is 92 KB, loaded only by a page that shows Arabic letters, and then cached.
- Good, because it is static files served by the existing route, under the existing security policy, as Inter is.
- Bad, because the WOFF2 is converted from the release's TTF by us (a one-time, lossless step recorded in `VERSION`), and updates are by hand.

### Option B: IBM Plex Sans Arabic
- Good, because it is also under the OFL, and well drawn.
- Bad, because it ships static weights only, so three files instead of one, and its proportions are drawn to match IBM Plex Sans, not Inter.

### Option C: the system's Arabic fonts
- Good, because nothing is added.
- Bad, because the page looks different on each system, and some Linux systems have no good Arabic font at all.

### Option D: load an Arabic font from Google Fonts or another CDN
- Bad, because the page would call an outside service, which non-negotiable 4 rules out, and the security policy would have to allow it.

## Decision
Option A. Codetrail bundles `NotoSansArabic-wght.woff2`, converted losslessly from Noto Sans Arabic 2.013's unhinted variable font, with the OFL text, in `src/codetrail/web/static/vendor/noto-sans-arabic/`. It is declared with `@font-face` (weights 400 to 700, `font-display: swap`) and a `unicode-range` over the Arabic blocks, and named after Inter in the page's font stack, so Latin text and digits stay in Inter and Arabic letters use Noto Sans Arabic. The source, the conversion and the SHA-256 sums are recorded in the folder's `VERSION` file, and a test checks the sums, as for Inter.

## Consequences
- Arabic text (the interface, answers and feedback) looks as designed on every system. Once Arabic ships, every page loads the font once, because the language picker names Arabic in Arabic; a release without Arabic loads nothing more.
- The package grows by about 100 KB.
- Updating the font is a manual step: download the release, convert, and update `VERSION` and its sums.
- Revisit if Noto's licence changes, if the design moves away from Inter, or if another script needs a bundled font (one ADR per typeface keeps this rule).

## Changes required
- [ ] The font file, its licence and `VERSION` in `static/vendor/noto-sans-arabic/`; `@font-face` and the font stack in `static/css/tokens.css` (this change).
- [ ] `tests/unit/test_static_assets.py`: the file matches its recorded sum and its licence is there; a browser test checks the face loads for Arabic text under the page's security policy (this change).
- [ ] Design sections 12 and 16.2: the typeface and the vendor folder (this change).
- [ ] README: credit Noto Sans Arabic and its licence beside Inter (this change).
- [ ] `docs/adr/README.md`: this ADR in the index (this change).
