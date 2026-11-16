"""Runs the reader's own `gh`, `az` and `glab`, read-only and without a token (design section 24.3).

Every command is checked against a fixed list of reads before it runs, the program gets an allowlisted environment
(token variables never pass, so it uses its own saved sign-in), and arguments are a list, never a shell line.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from codetrail.assistant import AssistantError, runner
from codetrail.assistant.environment import resolve_program, safe_path
from codetrail.config import HostingSettings
from codetrail.hosting import HostingError
from codetrail.hosting.names import Service

# Never passed, whatever the environment holds. The environment is an allowlist, so this list is a second guard and
# the test's record of what the documentation and the spike named.
TOKEN_VARIABLES = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "AZURE_DEVOPS_EXT_PAT",
        "SYSTEM_ACCESSTOKEN",
        "GITLAB_TOKEN",
        "GITLAB_ACCESS_TOKEN",
        "CI_JOB_TOKEN",
        "OAUTH_TOKEN",
        "GITLAB_CLIENT_ID",
        "GLAB_CLIENT_CERT",
        "CLIENT_CERT",
        "GLAB_CLIENT_KEY",
        "CLIENT_KEY",
        "GLAB_CLIENT_CERT_PASSWORD",
    }
)

ENVIRONMENT_NAMES = frozenset(
    {"HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "TERM", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"}
    | {"GH_CONFIG_DIR", "AZURE_CONFIG_DIR", "GLAB_CONFIG_DIR"}
    | {name for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY") for name in (proxy, proxy.lower())}
)

# The argument prefixes each service's program may run (design section 24.3, "Settled by the Phase 21 spike").
READS: dict[Service, tuple[tuple[str, ...], ...]] = {
    "github": (
        ("auth", "status", "--json", "hosts"),
        ("repo", "list"),
        ("api", "--method", "GET"),
    ),
    "azure_devops": (
        ("account", "show", "-o", "json"),
        ("devops", "project", "list"),
        ("repos", "list"),
        ("repos", "ref", "list"),
    ),
    "gitlab": (
        ("auth", "status", "--output", "json"),
        ("api", "--method", "GET"),
    ),
}

# Status commands take no further arguments (`--show-token`, `-t` would print the token).
EXACT_READS = {
    ("auth", "status", "--json", "hosts"),
    ("account", "show", "-o", "json"),
    ("auth", "status", "--output", "json"),
}

# After `api --method GET` only these flags pass, with exactly one path: a bundled short flag such as `-iXPOST`
# could change the method.
API_FLAGS = ("--paginate",)
API_FLAGS_WITH_VALUE = ("--jq",)

# After `repo list` only the owner and these flags pass: `--web` would open a browser.
REPO_LIST_FLAGS_WITH_VALUE = ("--json", "--limit")

# After an `az` read's prefix only the flags the adapter passes, each with exactly one value: `--debug` would write
# the requests to standard error, and `--query` or another flag would change what the read returns.
AZURE_FLAGS = ("--organization", "--project", "--repository", "--filter")
AZURE_OUTPUT_FLAGS = ("-o", "--output")  # and only as `json`


def hosting_environment(environ: Mapping[str, str]) -> dict[str, str]:
    environment = {name: environ[name] for name in ENVIRONMENT_NAMES - TOKEN_VARIABLES if name in environ}
    environment |= {name: value for name, value in environ.items() if name.startswith("LC_")}
    environment["PATH"] = safe_path(environ.get("PATH", ""))
    return environment


def is_read(service: Service, arguments: Sequence[str]) -> bool:
    for prefix in READS[service]:
        if tuple(arguments[: len(prefix)]) == prefix:
            if prefix in EXACT_READS:
                return len(arguments) == len(prefix)
            rest = arguments[len(prefix) :]
            if service == "azure_devops":
                return _only_allowed_azure_flags(rest)
            if prefix[0] == "api":
                return _only_allowed_flags(rest, API_FLAGS, API_FLAGS_WITH_VALUE)
            return _only_allowed_flags(rest, (), REPO_LIST_FLAGS_WITH_VALUE)
    return False


def _only_allowed_azure_flags(rest: Sequence[str]) -> bool:
    if len(rest) % 2:
        return False
    for flag, value in zip(rest[::2], rest[1::2], strict=True):
        if value.startswith("-"):  # a flag where its value should be
            return False
        if flag not in AZURE_FLAGS and not (flag in AZURE_OUTPUT_FLAGS and value == "json"):
            return False
    return True


def _only_allowed_flags(rest: Sequence[str], flags: Sequence[str], flags_with_value: Sequence[str]) -> bool:
    """Only the given flags, each flag with a value followed by one that isn't a flag, and exactly one positional
    argument (the API path, or the owner whose repositories are listed)."""
    positionals = 0
    position = 0
    while position < len(rest):
        argument = rest[position]
        if argument in flags_with_value:
            position += 1  # its one value
            if position >= len(rest) or rest[position].startswith("-"):
                return False
        elif argument.startswith("-"):
            if argument not in flags:
                return False
        else:
            positionals += 1
        position += 1
    return positionals == 1


def run_read(
    service: Service,
    program: str,
    arguments: Sequence[str],
    settings: HostingSettings,
    environ: Mapping[str, str] = os.environ,
) -> str:
    """The program's output for a read, or a HostingError. The error names the program and its last error line,
    never the arguments, which may hold names. A program that writes more than `max_output_bytes` is stopped."""
    if not is_read(service, arguments):
        raise HostingError(f"That command isn't a read, so it isn't run ({service}).")
    environment = hosting_environment(environ)
    name = Path(program).name
    errors: list[str] = []
    try:
        path = resolve_program(program, environment["PATH"])
        code, output = runner.run_command(
            [path, *arguments],
            Path("/"),
            environment,
            settings.timeout_seconds,
            error_output=errors,
            max_output_bytes=settings.max_output_bytes,
        )
    except AssistantError as error:
        raise HostingError(str(error)) from error
    if code != 0:
        last = next((line for line in reversed("".join(errors).splitlines()) if line.strip()), "")
        raise HostingError(f"{name} stopped with exit code {code}: {last.strip()[: runner.ERROR_LINE_CHARS]}")
    return output
