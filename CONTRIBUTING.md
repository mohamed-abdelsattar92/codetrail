# Contributing to Codetrail

Thank you for helping. Bug reports, ideas, documentation and code are all welcome. These rules keep Codetrail simple, safe and easy to review; every pull request is checked against them.

To report a security problem, don't open an issue: follow [SECURITY.md](SECURITY.md).

## Set up
Once per clone: `mise trust && mise install`, then `just setup` (the Python and commit-message dependencies, the git hooks and the git-flow settings). Everything runs through `just`; `just ci` runs what CI runs.

## Workflow
1. **Discuss first.** For anything larger than a small fix, open an issue before you write it, so that we agree on the approach.
2. **Use git-flow.** Branch features off `develop` with `git flow feature start <name>`, and fixes with `git flow bugfix start <name>`. One topic per branch. Never branch from `main`; `release/` and `hotfix/` branches are the maintainer's.
3. **Every contribution goes through a pull request.** Push your branch to your own fork and open a pull request into `develop`. Nobody pushes to `main`: releases reach it through a pull request from `release/<version>` (see [Releasing](#releasing)).
4. **Keep pull requests small.** One topic, as small as it can be while complete.
5. **CI passes and the maintainer reviews** before anything is merged. Pull requests are merged with a merge commit, never squashed or rebased, so each commit and its message stay in the history.

## Writing code
6. **Keep it simple.** Write the plainest code that meets the need. No speculative features, options or helpers "for later". Reuse what the repository and its libraries already have. Add an interface or a layer only when it has two real callers. Name things for what they are or do.
7. **Simplify before you commit.** Reread your diff before every commit: remove what isn't needed, reuse what already exists, and simplify what's left.
8. **Test first.** Write a failing test, then the code that passes it. Refusals and errors are behaviours too, and get tests.
9. **Use each tool's standard structure:** a uv project, FastAPI routers, `sqlite3`. No custom frameworks or clever metaprogramming.

## Commits and documents
10. **Conventional Commits with a scope**, such as `fix(web): …`, and a body with the sections What, Why, Alternatives considered, Risks, and Agent and model ("none" if you wrote it yourself). commitlint checks this in the commit-msg hook.
11. **Never skip the git hooks.** No `--no-verify`, no `git commit -n`. If a hook is wrong, say so in the pull request instead.
12. **Update the documents** your change affects, starting with the README, and add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.

## Security and scope
13. **Know the security checklist.** Every change is reviewed against the [security review checklist](docs/security/review-checklist.md). Never commit a secret, and remember that Codetrail never writes to a repository it reads.
14. **Propose before you add.** A new dependency, top-level folder or external service needs an [architecture decision record](docs/adr/README.md) with status `proposed` first.
15. **Report security problems privately**, as [SECURITY.md](SECURITY.md) explains, never in a public issue.

## Coding assistants
16. **Assistants are welcome.** Name the agent and model in each commit's "Agent and model" section. Assistants follow [AGENTS.md](AGENTS.md) wherever they run: they never push, open pull requests or release, so you push and open the pull request yourself. You're responsible for everything you submit, whoever wrote it.

## Licence
17. **Contribute under the Apache License 2.0.** Codetrail is licensed under the [Apache License 2.0](LICENSE). Whatever you submit for inclusion is licensed under the same terms (section 5 of the licence), with no separate agreement to sign. You keep the copyright in your work. Only submit work you have the right to: where it includes another person's work, say so in the pull request, with its source and its licence.

The name and logo aren't covered by the licence; see [TRADEMARKS.md](TRADEMARKS.md).

## Releasing
For the maintainer. `main` accepts changes only through a pull request that passes CI, so a release never uses `git flow release finish`, which merges on your machine. A release branch carries no commits of its own: a fix found while releasing goes into `develop` on a bugfix branch, and the release starts again from there. A hotfix goes through a pull request into `main` too, never `git flow hotfix finish`, and `main` is then merged back into `develop` as in step 5.

1. On a feature branch into `develop`, set the version in `pyproject.toml` and `src/codetrail/__init__.py` (then `uv lock`), move **Unreleased** in `CHANGELOG.md` under the new version, and write `docs/releases/v<version>.md`.
2. `git flow release start <version>`, then `git push -u origin release/<version>`.
3. `gh pr create --base main --head release/<version> --title "Release <version>"`, and when CI passes, `gh pr merge --merge`.
4. `git checkout main && git pull`, then `git tag -a v<version> -m "Codetrail <version>"` and `git push origin v<version>`.
5. Bring the release back: `git checkout develop && git merge --no-ff main && git push origin develop`, then delete `release/<version>` locally and on GitHub.
6. `gh release create v<version> --draft --verify-tag --title "Codetrail <version>" --notes-file docs/releases/v<version>.md`, check it, and publish it with `gh release edit v<version> --draft=false`. With the repository's release immutability setting on, as it is, a published release and its tag can't be changed.
