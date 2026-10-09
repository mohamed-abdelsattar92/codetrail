# codetrail task runner (decision 11). Run `just` to list the recipes.
# First run on a new clone: `mise trust && mise install`, then `just setup`.

set shell := ["bash", "-euo", "pipefail", "-c"]

# List every recipe
default:
    @just --list --unsorted

# Install the pinned tools, the Python and commit-message tooling, the git hooks and the git-flow config
setup:
    mise install
    uv sync
    uv run playwright install chromium
    pnpm install --frozen-lockfile
    just _install-hooks
    git flow config sync
    @echo "Setup complete."

# Run every check CI runs
ci: check-repo lint typecheck test test-browser

# Repository-wide checks: secrets anywhere in the history, and the file rules on every tracked file
check-repo:
    gitleaks git --redact --no-banner
    git ls-files -z | xargs -0 python3 tools/hooks/check_files.py

# Check the commit messages between two commits; CI passes commitlint.ci.config.mjs, which accepts Dependabot's too
check-commits from to="HEAD" config="commitlint.config.mjs":
    pnpm exec commitlint --config {{ config }} --from {{ from }} --to {{ to }} --verbose

# Release a version from develop, from the founder's terminal: the pull request into main, the tag and a draft GitHub release (CONTRIBUTING.md, Releasing)
[positional-arguments]
release version:
    python3 tools/release.py "$1"

# Check formatting and lint everything
lint: lint-just lint-python

# Check the justfile's formatting
lint-just:
    just --fmt --check --unstable

# Lint and check the formatting of the Python code
lint-python:
    uv run ruff check
    uv run ruff format --check

# Apply every formatter
format:
    just --fmt --unstable
    uv run ruff check --fix
    uv run ruff format

# Type-check the package and its tests (mypy strict)
typecheck:
    uv run mypy

# Extract the interface's strings to the template, update every language's catalog, and compile them (ADR 0005)
catalogs:
    uv run pybabel extract --no-location --omit-header --sort-output -F babel.cfg -k pgettext:1c,2 -o src/codetrail/locales/codetrail.pot .
    if ls src/codetrail/locales/*/LC_MESSAGES/codetrail.po >/dev/null 2>&1; then uv run pybabel update --no-location --omit-header -i src/codetrail/locales/codetrail.pot -d src/codetrail/locales -D codetrail; fi
    if ls src/codetrail/locales/*/LC_MESSAGES/codetrail.po >/dev/null 2>&1; then uv run pybabel compile -d src/codetrail/locales -D codetrail; fi

# Run every test except the live ones (the real providers) and the browser ones (just test-browser)
test:
    uv run pytest -q

# Run the unit, API and tool tests in parallel, as the pre-push hook does (ADR 0011)
test-quick:
    uv run pytest -q -n auto tests/unit tests/api tools/tests

# Run the page in headless Chromium: the palette, shortcuts, the Ask panel and accessibility (ADR 0008)
test-browser:
    uv run pytest -q -m browser tests/browser

# Run the live tests against the real Claude (local only; CI has no Claude sign-in)
test-live:
    uv run pytest -q -m live --override-ini addopts=

_install-hooks:
    #!/usr/bin/env bash
    set -euo pipefail
    hooks=$(git rev-parse --git-path hooks)
    mkdir -p "$hooks"
    for f in agent-push-guard run-lefthook pre-commit commit-msg pre-push; do
        install -m 755 "tools/git-hooks/$f" "$hooks/$f"
    done
    echo "Git hooks installed in $hooks."
