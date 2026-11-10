"""Mirrors of remote targets: git fetches over https or ssh with the reader's own sign-in (design section 24.4).

Codetrail sets no credential helper, writes no git configuration and passes no token. git never prompts, and its own
messages are never shown, since they can quote a URL with credentials.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from codetrail.config import TargetConfig
from codetrail.errors import CodetrailError
from codetrail.hosting.names import Service, check_clone_url
from codetrail.hosting.run import hosting_environment
from codetrail.repo.git import run_git
from codetrail.repo.mirror import refresh_mirror, refresh_mirror_from

# Only code widens this; no setting does. Tests add "http" to reach a server on 127.0.0.1.
REMOTE_PROTOCOLS = ("https", "ssh")

SIGN_IN_FIXES: dict[Service, str] = {
    "github": "gh auth setup-git",
    "gitlab": "glab auth login",
    "azure_devops": "Git Credential Manager or an SSH key",
}


def protocol_options(protocols: Sequence[str]) -> list[str]:
    # file and ext are named too: a `protocol.<name>.allow` in the reader's own git configuration would otherwise
    # override `protocol.allow=never`.
    options = ["-c", "protocol.allow=never", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never"]
    for protocol in protocols:
        options += ["-c", f"protocol.{protocol}.allow=always"]
    return options


def remote_options(protocols: Sequence[str], stall_seconds: int) -> list[str]:
    """git's `-c` options for a remote: the protocols, no askpass program (git would run one before it checks
    GIT_TERMINAL_PROMPT) and a stop for an http transfer that stalls (slower than 1000 bytes a second)."""
    return [
        *protocol_options(protocols),
        *["-c", "core.askPass="],
        *["-c", "http.lowSpeedLimit=1000", "-c", f"http.lowSpeedTime={stall_seconds}"],
    ]


def remote_git_environment(environ: Mapping[str, str], stall_seconds: int) -> dict[str, str]:
    """The hosting programs' allowlist, plus what keeps an ssh-agent and a corporate certificate working."""
    environment = hosting_environment(environ)
    environment |= {name: environ[name] for name in ("SSH_AUTH_SOCK", "GIT_SSL_CAINFO") if name in environ}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_CEILING_DIRECTORIES"] = "/"
    environment["GIT_SSH_COMMAND"] = (
        f"ssh -o BatchMode=yes -o ConnectTimeout={stall_seconds} "
        f"-o ServerAliveInterval={stall_seconds} -o ServerAliveCountMax=1"
    )
    return environment


def refresh_remote_mirror(
    mirror: Path,
    url: str,
    branch: str,
    *,
    sign_in_fix: str,
    stall_seconds: int,
    protocols: Sequence[str] = REMOTE_PROTOCOLS,
) -> str:
    """Clones or fetches the remote's branch into the mirror and returns its head commit. A transfer that stalls
    for `stall_seconds` fails, however long a healthy one runs."""
    options = remote_options(protocols, stall_seconds)
    environment = remote_git_environment(os.environ, stall_seconds)
    failure = CodetrailError(f"git couldn't fetch {url}. If it needs your sign-in, run: {sign_in_fix}")
    try:
        found = run_git(
            [*options, "ls-remote", "--heads", "--", url, f"refs/heads/{branch}"],
            allowed_exit_codes=(0, 2),
            environment=environment,
        )
    except CodetrailError:
        raise failure from None
    if not found.strip():
        raise CodetrailError(f"{url} has no branch {branch!r}.")
    try:
        return refresh_mirror_from(mirror, url, branch, git_options=options, environment=environment)
    except CodetrailError:
        raise failure from None


def fetch_target(mirror: Path, target: TargetConfig, *, stall_seconds: int) -> str:
    """Refreshes the target's mirror, from its checkout or from its service, and returns the head commit."""
    if target.remote is None:
        assert target.repository is not None  # exactly one of the two is set
        return refresh_mirror(mirror, target.repository, target.branch)
    url = check_clone_url(target.remote.service, target.remote.url)  # again, before every fetch
    return refresh_remote_mirror(
        mirror, url, target.branch, sign_in_fix=SIGN_IN_FIXES[target.remote.service], stall_seconds=stall_seconds
    )
