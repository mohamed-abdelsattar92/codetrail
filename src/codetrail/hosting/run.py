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

# After `api --method GET`, nothing may change the method or send a body.
API_WRITE_FLAGS = ("--method", "--field", "--raw-field", "--input", "-X", "-f", "-F")


def hosting_environment(environ: Mapping[str, str]) -> dict[str, str]:
    environment = {name: environ[name] for name in ENVIRONMENT_NAMES - TOKEN_VARIABLES if name in environ}
    environment |= {name: value for name, value in environ.items() if name.startswith("LC_")}
    environment["PATH"] = safe_path(environ.get("PATH", ""))
    return environment


def is_read(service: Service, arguments: Sequence[str]) -> bool:
    for prefix in READS[service]:
        if tuple(arguments[: len(prefix)]) == prefix:
            rest = arguments[len(prefix) :]
            return prefix[0] != "api" or not any(argument.startswith(API_WRITE_FLAGS) for argument in rest)
    return False


def run_read(
    service: Service,
    program: str,
    arguments: Sequence[str],
    settings: HostingSettings,
    environ: Mapping[str, str] = os.environ,
) -> str:
    """The program's output for a read, or a HostingError. The error names the program and its last error line,
    never the arguments, which may hold names."""
    if not is_read(service, arguments):
        raise HostingError(f"That command isn't a read, so it isn't run ({service}).")
    environment = hosting_environment(environ)
    name = Path(program).name
    errors: list[str] = []
    try:
        path = resolve_program(program, environment["PATH"])
        code, output = runner.run_command(
            [path, *arguments], Path("/"), environment, settings.timeout_seconds, error_output=errors
        )
    except AssistantError as error:
        raise HostingError(str(error)) from error
    if code != 0:
        last = next((line for line in reversed("".join(errors).splitlines()) if line.strip()), "")
        raise HostingError(f"{name} stopped with exit code {code}: {last.strip()[:300]}")
    if len(output.encode("utf-8")) > settings.max_output_bytes:
        raise HostingError(f"{name} returned more than {settings.max_output_bytes} bytes, so it isn't used.")
    return output
