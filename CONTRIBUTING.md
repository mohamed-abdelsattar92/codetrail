# Contributing to Codetrail

Thank you for helping. Bug reports, ideas, documentation and code are all welcome.

## Before you start
- For anything larger than a small fix, open an issue first, so that we can agree on the approach before you write it.
- Read [AGENTS.md](AGENTS.md) for the project's rules on code, tests and security. The [security review checklist](docs/security/review-checklist.md) is what every change is reviewed against. Coding agents follow AGENTS.md's "Never do" rules wherever they run: they never push or open pull requests. If you contribute by hand, you push to your own fork and open the pull request yourself.

## Making a change
1. Set up once per clone: `mise trust && mise install`, then `just setup`.
2. Branch off `develop`: `git flow feature start <name>`.
3. Write a failing test first, then the code that passes it.
4. Commit with Conventional Commits and a scope. Every body has the sections What, Why, Alternatives considered, Risks, and Agent and model ("none" if you wrote it yourself). commitlint checks this.
5. Update the documents your change affects, starting with the README.
6. Run `just ci` until it passes, then open a pull request against `develop`.

## Licence and the CLA
Codetrail is licensed under the [GNU AGPL 3.0](LICENSE), and its copyright holder also sells commercial licences. Your contribution can only be merged once you have agreed to the [contributor licence agreement](CLA.md) by ticking its box in the pull request. You keep the copyright in your work. The agreement lets the project ship it under both the AGPL and its commercial licences.

The name and logo aren't covered by the licence; see [TRADEMARKS.md](TRADEMARKS.md).
