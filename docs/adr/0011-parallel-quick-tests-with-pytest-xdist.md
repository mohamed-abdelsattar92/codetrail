# 0011. Run the pre-push hook's quick tests in parallel with pytest-xdist, on the unit, API and hook tests only

- Status: proposed
- Date: 2026-10-07
- Deciders: KoGy
- Proposed by: Claude Code (Claude Opus 5.5), after the founder asked on 2026-10-07 to make `just test-quick` quick
- Design: `docs/design/2026-10-05-codetrail-design.md` sections 11 (recipes) and 12 (dependencies); brainstorm decision 11 (the pre-push hook runs the quick tests)

## Context
The founder's `git push` to GitHub over SSH failed with "Connection to github.com closed by remote host". git opens the connection, runs the pre-push hook, and only then sends the commits. The hook's `just test-quick` took 409 seconds, and GitHub closed the idle connection while it ran. The tests passed, but nothing was pushed.

Facts (measured on 2026-10-07, on the founder's 11-core Mac):
- `test-quick` selected `-m "not slow and not live and not browser"`, but no test was marked `slow`. It therefore ran the integration and end-to-end tests too, although section 11 says the quick tests are "unit and API". Those two folders took about 112 seconds.
- Fixture setup takes 209 of the 409 seconds. Most API tests build a guide with `run_update` and the fake assistant (about 2.4 seconds each). Three quarters of that is gitleaks, which runs about seven times per update at about 0.25 seconds each. That is the real secret filter, and the tests should keep exercising it.
- With pytest-xdist on the unit, API and hook tests (1,341 tests), the run took 84 to 104 seconds while other sessions loaded the machine (load average 14), against 409 before.
- pytest-xdist is maintained by the pytest-dev organization under the MIT licence. Its only dependency is execnet, also MIT and maintained by pytest-dev (https://github.com/pytest-dev/pytest-xdist).

## Decision drivers
- The pre-push hook finishes well before GitHub drops an idle connection.
- The quick tests still cover the unit and API behaviour, refusals included, with the real secret filter.
- Nothing is added to Codetrail's runtime.
- No test is changed to make it faster.

## Options considered
### Option A: the unit, API and hook test folders, in parallel with pytest-xdist
- Good, because the selection matches section 11, and a new integration or end-to-end test can't slip into the hook.
- Good, because the tests themselves don't change, and pytest-xdist is the standard way to run pytest in parallel.
- Bad, because it adds a development dependency (with execnet), and failures in parallel runs print a little less tidily.

### Option B: mark the integration and end-to-end tests `slow`, without parallel runs
- Good, because it adds nothing.
- Bad, because the run still takes about 5 minutes, close to the time limit that failed.

### Option C: build each API test file's guide once and share it
- Good, because it removes most of the setup time without a dependency.
- Bad, because pages record what the reader opened, so shared state would make the tests depend on their order. It is a large change to many test files.

### Option D: replace gitleaks with a stub in the API tests
- Bad, because the secret filter is a security control (non-negotiable 1), and the API tests would stop exercising it.

## Decision
Option A. `just test-quick` runs `pytest -q -n auto tests/unit tests/api tools/tests`. The unused `slow` marker is removed. `just test` and `just ci` are unchanged: they still run every test, including integration and end-to-end, one at a time.

## Consequences
- The hook takes about a minute and a half instead of seven, and less on an idle machine.
- The integration and end-to-end tests run in `just test`, `just ci` and GitHub's CI, no longer before each push.
- A test that shares state with another test could fail only in parallel. The unit, API and hook tests write only to their own `tmp_path`, and passed in parallel every time they were run.
- Revisit if the hook again approaches GitHub's time limit; Option C is the next step.

## Changes required
- [x] `pyproject.toml` and `uv.lock`: `pytest-xdist==3.8.0` in the `dev` group; the `slow` marker removed.
- [x] `justfile`: `test-quick`.
- [x] Design: sections 11 and 12.
- [x] README: what `just test-quick` runs.
