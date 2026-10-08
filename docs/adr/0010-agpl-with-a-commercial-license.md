# 0010. License Codetrail under the AGPL 3.0 only, sell commercial licenses, and reserve the name and logo

- Status: superseded by 0012
- Date: 2026-10-07
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder chose "AGPL with a commercial license" on 2026-10-07
- Design: none of its sections; the repository's licensing, contribution and brand rules

## Context
The repository has no licence, so nobody may use, change or share its code, and nobody can contribute under clear terms. The founder wants:
- the code open source, so that people can read it and contribute;
- the name "Codetrail" and its logo used only with the founder's written permission;
- commercial use only with the founder's written permission.

Facts (checked on 2026-10-07):
- The Open Source Definition forbids restricting a field of use (criterion 6, https://opensource.org/osd), so a licence that forbids commercial use isn't open source. Dual licensing gets close: an open source licence for everyone, and a separate commercial licence for those who won't accept its terms.
- The GNU AGPL 3.0 is the GPL 3.0 plus section 13: whoever runs a modified version for users over a network must offer those users its source. Businesses that won't publish their changes, including in a hosted service, need another licence, which only the copyright holder can grant (https://www.gnu.org/licenses/agpl-3.0.html).
- AGPL section 7(e) lets the licensor decline to grant rights under trademark law. A copyright licence never grants trademark rights anyway, and 7(e) states this in the licence itself.
- The copyright holder can only sell commercial licences for code they own or have the right to sublicense. Each outside contribution therefore needs a contributor licence agreement (CLA) granting that right.
- Codetrail's runtime dependencies and vendored files are under MIT, BSD, Apache 2.0 and MPL 2.0 licences, plus the SIL Open Font License 1.1 for Inter (ADRs 0001, 0004, 0007, 0008). All of them can be combined with the AGPL 3.0.

## Decision drivers
- The founder keeps control of commercial use.
- The code stays open source (OSI-approved), so contributors trust it.
- The name and logo stay the founder's.
- Contributing stays easy, with no outside service.

## Options considered
### Option A: the AGPL 3.0 only, a commercial licence on request, a CLA for contributions, and a trademark policy
- Good, because it is an OSI-approved licence, and any business that would rather not publish its changes needs the founder's commercial licence.
- Good, because "only" keeps the terms fixed: a later AGPL version the Free Software Foundation publishes doesn't apply unless the founder chooses it.
- Bad, because some contributors refuse CLAs, and some companies forbid AGPL code outright.
- Bad, because the AGPL doesn't stop commercial use: a company that complies with it (publishing its changes) needs no permission.

### Option B: the AGPL 3.0 or any later version
- Good, because it combines with code licensed "GPL 3.0 or later".
- Bad, because the Free Software Foundation, not the founder, writes those later versions.

### Option C: PolyForm Noncommercial 1.0.0
- Good, because any commercial use needs written permission, which is exactly the rule the founder stated.
- Bad, because it isn't open source; fewer people contribute, and distributions and companies avoid it.

### Option D: Apache 2.0 with a trademark policy
- Good, because it is the easiest licence for contributors and companies to accept.
- Bad, because anyone may use the code commercially without asking, which the founder doesn't want.

## Decision
Option A. Codetrail is licensed under `AGPL-3.0-only`, with the copyright held by Mohamed Abdel Sattar. Commercial licences are granted in writing, on request to mohamed.abdelsattar92@hotmail.com. Contributors accept `CLA.md` by ticking its box in the pull request template. `TRADEMARKS.md` reserves the name and logo, under AGPL section 7(e). It is the only option that is both open source and keeps commercial use in the founder's hands.

## Consequences
- The code can be read, changed and shared under the AGPL. A business that wants to keep its changes private, or to ship Codetrail in a closed product, needs the founder's commercial licence.
- A copy that someone changes and runs as a service for other people must offer those people its source (AGPL section 13). An unchanged Codetrail on the reader's own machine owes nothing: its page listens on `127.0.0.1` for the reader alone.
- Each outside pull request needs its CLA box ticked before it's merged. The record lives in the pull request on GitHub; if one is ever disputed, the founder can switch to a signatures file or a CLA service with a new ADR.
- Forks must rename themselves and drop the logo.
- `CLA.md`, `TRADEMARKS.md` and the commercial terms are drafts written by a coding agent, not by a lawyer. A lawyer should review them before the first outside contribution or the first commercial licence.
- Revisit if contributions stall because of the CLA, or if the founder stops selling commercial licences.

## Changes required
- [x] `LICENSE`: the AGPL 3.0's official text, unchanged.
- [x] `pyproject.toml`: `license = "AGPL-3.0-only"` and `license-files`.
- [x] `TRADEMARKS.md`, `CLA.md`, `CONTRIBUTING.md` and `.github/pull_request_template.md`.
- [x] README: a License section, and the new documents in its Documentation table.
- [ ] A lawyer reviews `CLA.md`, `TRADEMARKS.md` and the commercial terms (the founder).
- [ ] Optionally, register the name and logo as trademarks (the founder).
