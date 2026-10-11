# 0015. Run the whole test suite in parallel with pytest-xdist, and give CI 30 minutes

- Status: proposed
- Date: 2026-10-11
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), while preparing release 1.2.0 on 2026-10-11, when the founder approved the fix
- Design: `docs/design/2026-10-05-codetrail-design.md` section 11 (recipes); [ADR 0011](0011-parallel-quick-tests-with-pytest-xdist.md), which this widens

## Context
CI's one job, `just ci`, has a 15-minute timeout. Every push to `develop` since release 1.1.1 was cancelled at that limit, five runs from the documentation metrics' merge on 2026-10-10 to the sidebar's polish on 2026-10-11, so CI hasn't passed on `develop` since then, and a release pull request into `main` would be cancelled the same way.

Facts (measured on 2026-10-11, on the founder's 11-core Mac):
- `just test` (everything but the live and browser tests, 1,666 tests) runs serially in about 12 minutes; `just test-browser` (123 tests) in about 8 to 10. With the repository checks, lint and type checks, `just ci` takes about 20 minutes, before CI's own setup (mise, pnpm, uv and Chromium).
- With pytest-xdist, `pytest -n auto` runs the same 1,666 tests in 3.5 minutes, and the browser tests on four workers in 5 minutes. All pass: every test builds its own data in its own temporary folder, and every browser test serves the page on a free port of its own.
- GitHub's `ubuntu-24.04` runner has 4 cores, so `-n auto` gives four workers there.
- pytest-xdist is already a development dependency ([ADR 0011](0011-parallel-quick-tests-with-pytest-xdist.md)), used by `just test-quick` and the pre-push hook.

## Decision drivers
- CI passes on `develop` and on a release pull request, with room for the suite to keep growing.
- No test is changed or skipped to make it faster.
- Nothing is added to Codetrail's runtime or to the development dependencies.

## Options considered
### Option A: `-n auto` for `just test` and `just test-browser`, and a 30-minute timeout
- Good, because CI's tests take about a third of the time, and the timeout leaves room beyond that.
- Good, because it uses a dependency the project already has, as the pre-push hook does.
- Bad, because a failure in a parallel run prints a little less tidily, and a test that one day shares state with another fails only now and then.

### Option B: raise the timeout to 40 minutes and keep the tests serial
- Good, because it's one line.
- Bad, because every CI run takes over 20 minutes, and the limit will be reached again as the suite grows.

### Option C: split CI into a job per kind of test
- Good, because the jobs run side by side.
- Bad, because each job repeats the setup (mise, pnpm, uv, Chromium), and the workflow becomes harder to follow.

## Decision
Option A: `just test` and `just test-browser` run with `-n auto`, and CI's job has `timeout-minutes: 30`. It makes CI pass with room to spare, and changes neither a test nor a dependency.

## Consequences
- CI finishes in roughly 15 minutes instead of being cancelled, and `just ci` takes about 9 minutes locally instead of 20.
- A test that writes outside its own temporary folder or binds a fixed port would fail now and then; tests already avoid both, and a new one has to as well.
- Revisit if CI again comes within five minutes of its timeout, or if a parallel run fails where a serial one passes.

## Changes required
- [x] `justfile`: `test` and `test-browser` run with `-n auto`.
- [x] `.github/workflows/ci.yml`: `timeout-minutes: 30`.
- [x] Design section 11 and the README describe the recipes as parallel.
- [x] `docs/adr/README.md` lists this record.
