# 0012. License Codetrail under the Apache License 2.0, take contributions under the same licence, and keep the name and logo reserved

- Status: accepted
- Date: 2026-10-08
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder asked on 2026-10-08 to change the licence to the Apache License 2.0 and to drop the CLA
- Design: none of its sections; the repository's licensing, contribution and brand rules. Replaces ADR 0010.

## Context
ADR 0010 (proposed) licensed Codetrail under the AGPL 3.0 only, offered commercial licences on request, and asked contributors to accept a CLA so those commercial licences could cover their code. Versions 1.0.0 and 1.0.1 were released under it. The founder now wants the Apache License 2.0 instead.

- Every commit so far is by the founder, who holds the whole copyright and can relicense alone. Copies of 1.0.0 and 1.0.1 already shared stay under the AGPL; the new licence applies from the next release.
- The Apache License 2.0 is permissive and OSI-approved: anyone may use, change and share the code, commercially or not, as long as they keep the copyright, licence and NOTICE notices and mark the files they changed (section 4). It grants a patent licence from each contributor, which ends for anyone who sues over patents in the work (section 3) (https://www.apache.org/licenses/LICENSE-2.0).
- Section 5 makes every contribution submitted for inclusion licensed under the same terms, unless its author says otherwise ("inbound = outbound"). With no commercial licence to sell, a CLA has nothing left to enable.
- Section 6 grants no rights to the licensor's trade names or trademarks, so `TRADEMARKS.md` still holds.
- Codetrail's runtime dependencies and vendored files are under MIT, BSD, Apache 2.0 and MPL 2.0 licences, plus the SIL Open Font License 1.1 for Inter (ADRs 0001, 0004, 0007, 0008). All of them can be shipped alongside Apache 2.0 code; the vendored files keep their own licences next to them.

## Decision drivers
- The founder's choice of the Apache License 2.0.
- Easy for people and companies to use and to contribute to.
- A patent grant from contributors.
- The name and logo stay the founder's.

## Options considered
### Option A: the Apache License 2.0, contributions under section 5, no CLA, and the trademark policy kept
- Good, because it is the licence the founder asked for, and companies accept it widely.
- Good, because contributors sign nothing: submitting a pull request is enough.
- Bad, because anyone may use Codetrail commercially, or ship it in a closed product, without asking or sharing changes.
- Bad, because without a CLA, relicensing outside contributions later needs each contributor's consent.

### Option B: the Apache License 2.0, keeping the CLA
- Good, because the founder could relicense contributions later without asking.
- Bad, because it keeps friction for contributors, which the founder declined on 2026-10-08.

### Option C: keep ADR 0010 (AGPL with a commercial licence)
- Bad, because the founder chose to move away from it.

## Decision
Option A. Codetrail is licensed under `Apache-2.0`, with the copyright held by Mohamed Abdel Sattar. `LICENSE` holds the licence's official text, unchanged. Contributions are licensed under the same terms by section 5, and `CLA.md` is removed. `TRADEMARKS.md` keeps the name and logo reserved, citing section 6. The commercial licence offer ends.

## Consequences
- Using, changing and shipping Codetrail needs no permission, only the notices section 4 asks for.
- Pull requests no longer carry a CLA box; their template asks the contributor to confirm they may submit the work under the licence.
- Releases 1.0.0 and 1.0.1 stay under the AGPL 3.0 only; their release notes are left as published.
- `TRADEMARKS.md` is still a draft by a coding agent, not by a lawyer.
- Revisit if the founder wants to sell licences again, which would need a CLA or the consent of every outside contributor by then.

## Changes required
- [x] `LICENSE`: the Apache License 2.0's official text, unchanged.
- [x] `pyproject.toml`: `license = "Apache-2.0"`.
- [x] `CLA.md` removed; `CONTRIBUTING.md` and `.github/pull_request_template.md` describe contributing under section 5.
- [x] `TRADEMARKS.md`: cites the Apache License's section 6.
- [x] README: the License section and the Documentation table.
- [x] ADR 0010's status becomes `superseded by 0012`.
- [ ] A lawyer reviews `TRADEMARKS.md` (the founder).
