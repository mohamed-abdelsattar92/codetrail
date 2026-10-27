# 0019. Publish each release to PyPI as codetrail-cli, by trusted publishing from GitHub Actions

- Status: proposed
- Date: 2026-10-27
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder chose the name codetrail-cli on 2026-10-27
- Design: `docs/design/2026-10-05-codetrail-design.md` section 2 (install for daily use); `CONTRIBUTING.md`, Releasing; [ADR 0013](0013-commit-the-compiled-catalogs.md)

## Context
- A release installs today with `uv tool install git+https://github.com/mohamed-abdelsattar92/codetrail@v1.4.0`: a long command, a build from source on every install, and nothing a search on PyPI finds. Trying Codetrail should take one short command, because most people who find it decide in a minute or two.
- PyPI's `codetrail` belongs to an unrelated project (a version-control system inspired by Git, version 0.4.0, last released on 19 December 2024; checked on 2026-10-27). `codetrail-cli` is free. PEP 541 can transfer an abandoned name, but takes weeks to months.
- A distribution name and its import package can differ: uv's build backend takes the package's folder from `tool.uv.build-backend.module-name`, so the code stays `codetrail` and the command stays `codetrail`.
- PyPI's trusted publishing lets a named GitHub Actions workflow, in a named environment, upload with a short-lived token it gets for its run. No PyPI token is created or stored, and a project that doesn't exist yet can be set up as a "pending publisher", so the first upload creates it. `uv publish --trusted-publishing always` does the exchange itself, so no action beyond the two CI already pins is needed.
- The founder already publishes each GitHub release by hand (Releasing, step 6), and a published release and its tag can't be changed.

## Decision drivers
1. Security: no stored secret, and nothing but the founder's release can publish.
2. Trying Codetrail takes one short command.
3. No new step for the founder on each release, and no new dependency.

## Options considered
### Option A: keep installing from the git tag
- Good, because nothing changes and nothing new is trusted.
- Bad, because the command is long, PyPI search never finds Codetrail, and each install builds from source.

### Option B: the founder uploads each release from their machine with a PyPI token
- Good, because no workflow is added.
- Bad, because a long-lived token sits on the founder's machine and each release gains a manual step.

### Option C: a workflow publishes on each published release, by trusted publishing (chosen)
- Good, because no token exists anywhere, the upload is built from the tag the founder published, and the release needs nothing more.
- Good, because the `pypi` environment can require the founder's approval before each upload.
- Bad, because PyPI trusts the workflow's file name and environment, not a ref: anyone who can publish a release, or push a tag whose copy of the workflow is changed, could upload. So the `pypi` environment and a tag ruleset must hold that to the founder (Changes required).
- Bad, because the steps before the upload run in the job that can get the token. The job restores no cache, and its two actions and tools are pinned; splitting build and upload into two jobs would add two more actions to pin.

## Decision
Publish as `codetrail-cli` (the import package and the command stay `codetrail`) from `.github/workflows/publish.yml`, which runs when a GitHub release is published, checks that the tag names the version in `pyproject.toml`, builds with `uv build` and uploads with `uv publish --trusted-publishing always` in the `pypi` environment. It wins on all three drivers: no secret, `uvx --from codetrail-cli codetrail` to try it, and no new step per release.

## Consequences
- Easier: `uv tool install codetrail-cli` installs the latest release, and `uvx --from codetrail-cli codetrail --help` tries it without installing.
- Harder: anyone who installed with `uv tool install` from a clone or a tag has the tool named `codetrail`; they run `uv tool uninstall codetrail` once before installing `codetrail-cli`, because both put a `codetrail` command on the `PATH`. The release notes say so.
- The first release on PyPI is the next one: v1.4.0's tag still names the package `codetrail`, which PyPI would refuse.
- Revisit if PEP 541 gives Codetrail the `codetrail` name, or if releases stop going through GitHub.

## Changes required
- [x] `pyproject.toml`: `name = "codetrail-cli"`, `tool.uv.build-backend.module-name = "codetrail"`; `uv.lock`.
- [x] `.github/workflows/publish.yml`, and a test that the wheel is named `codetrail-cli`, has the `codetrail` command and holds every committed file of the package.
- [x] `docs/getting-started.md`: install from PyPI, and remove `codetrail-cli`.
- [x] `CONTRIBUTING.md`, Releasing: publishing a release uploads to PyPI, and the one-time setup.
- [ ] The founder, once, before the next release (`CONTRIBUTING.md`, Releasing):
  - on PyPI, add a pending trusted publisher: project `codetrail-cli`, owner `mohamed-abdelsattar92`, repository `codetrail`, workflow `publish.yml`, environment `pypi`;
  - on GitHub, create the `pypi` environment with the founder as its required reviewer and deployments limited to tags matching `v*`;
  - on GitHub, add a tag ruleset that lets only the founder create, update or delete `v*` tags.
