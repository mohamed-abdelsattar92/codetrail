"""Mirrors of remote targets: fetched over https or ssh with the reader's own git sign-in (design section 24.4)."""

from pathlib import Path

import pytest

from codetrail.config import TargetConfig
from codetrail.errors import CodetrailError
from codetrail.repo.git import run_git
from codetrail.repo.remote import (
    REMOTE_PROTOCOLS,
    fetch_target,
    protocol_options,
    refresh_remote_mirror,
    remote_git_environment,
)
from tests.fixtures.remote import SignInRequiredHandler, publish, serving, serving_repository
from tests.fixtures.repos import add_commit, git, make_repository

HTTP_ONLY = ("http",)
FIX = "gh auth setup-git"


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    return make_repository(tmp_path / "target", [{"README.md": "# Hello\n", "app/main.py": "print('hi')\n"}])


def tags_in(mirror: Path) -> list[str]:
    names = run_git(["for-each-ref", "--format=%(refname:strip=2)", "refs/tags"], git_dir=mirror)
    return sorted(names.decode().split())


def test_remote_git_gets_no_token_variables() -> None:
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/reader",
        "SSH_AUTH_SOCK": "/run/agent.sock",
        "GIT_SSL_CAINFO": "/etc/corporate.pem",
        "HTTPS_PROXY": "http://proxy.example:3128",
        "GH_TOKEN": "x",
        "GITHUB_TOKEN": "x",
        "AZURE_DEVOPS_EXT_PAT": "x",
        "GITLAB_TOKEN": "x",
        "GIT_ASKPASS": "/bin/steal",
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_CONFIG_COUNT": "1",
        "AWS_SECRET_ACCESS_KEY": "x",
    }
    environment = remote_git_environment(environ)
    assert environment["SSH_AUTH_SOCK"] == "/run/agent.sock"
    assert environment["GIT_SSL_CAINFO"] == "/etc/corporate.pem"
    assert environment["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert environment["HOME"] == "/home/reader"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CEILING_DIRECTORIES"] == "/"
    assert environment["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert set(environment) - {"PATH", "HOME", "SSH_AUTH_SOCK", "GIT_SSL_CAINFO", "HTTPS_PROXY"} == {
        "GIT_TERMINAL_PROMPT",
        "GIT_CEILING_DIRECTORIES",
        "GIT_SSH_COMMAND",
    }


def test_the_protocol_options_allow_only_the_named_protocols() -> None:
    assert protocol_options(REMOTE_PROTOCOLS) == [
        "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "url", ["file:///tmp/anywhere.git", "ext::sh -c 'touch /tmp/pwned'", "http://127.0.0.1:1/x.git"]
)
def test_file_ext_and_http_are_refused_by_default(url: str, tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.git"
    with pytest.raises(CodetrailError, match="couldn't fetch"):
        refresh_remote_mirror(mirror, url, "develop", sign_in_fix=FIX)
    assert not mirror.exists()


def test_a_remote_mirror_clones_and_fetches(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "data" / "mirror.git"
    git(checkout, "tag", "-a", "v1", "-m", "First")
    git(checkout, "tag", "old")
    with serving_repository(checkout, tmp_path / "served") as url:
        first = refresh_remote_mirror(mirror, url, "develop", sign_in_fix=FIX, protocols=HTTP_ONLY)
        assert first == git(checkout, "rev-parse", "HEAD")
        assert tags_in(mirror) == ["old", "v1"]
        second_commit = add_commit(checkout, {"app/extra.py": "x = 1\n"})
        git(checkout, "tag", "-d", "old")
        git(checkout, "tag", "v2")
        publish(checkout, tmp_path / "served")
        second = refresh_remote_mirror(mirror, url, "develop", sign_in_fix=FIX, protocols=HTTP_ONLY)
    assert second == second_commit
    assert tags_in(mirror) == ["v1", "v2"]


def test_a_missing_branch_is_named(checkout: Path, tmp_path: Path) -> None:
    with (
        serving_repository(checkout, tmp_path / "served") as url,
        pytest.raises(CodetrailError, match="no branch 'main'"),
    ):
        refresh_remote_mirror(tmp_path / "mirror.git", url, "main", sign_in_fix=FIX, protocols=HTTP_ONLY)


def test_a_fetch_that_needs_a_sign_in_names_the_fix(tmp_path: Path) -> None:
    with serving(SignInRequiredHandler) as base_url, pytest.raises(CodetrailError) as raised:
        refresh_remote_mirror(tmp_path / "mirror.git", f"{base_url}/x.git", "develop", sign_in_fix=FIX,
                              protocols=HTTP_ONLY)  # fmt: skip
    message = str(raised.value)
    assert FIX in message
    assert "@" not in message
    assert "401" not in message and "Authentication" not in message  # git's own words are never shown


def test_fetch_target_checks_the_url_again_and_names_the_services_fix(tmp_path: Path) -> None:
    remote = {"service": "github", "source": "acme-org", "id": "R_1", "name": "web-shop",
              "url": "https://github.com/acme-org/web-shop.git"}  # fmt: skip
    target = TargetConfig(remote=remote, branch="main")  # type: ignore[arg-type]
    object.__setattr__(target.remote, "url", "https://example.com/acme-org/web-shop.git")
    with pytest.raises(CodetrailError, match="github"):
        fetch_target(tmp_path / "mirror.git", target)
    assert not (tmp_path / "mirror.git").exists()


def test_fetch_target_reads_a_local_target_as_before(checkout: Path, tmp_path: Path) -> None:
    target = TargetConfig(repository=checkout, branch="develop")
    assert fetch_target(tmp_path / "mirror.git", target) == git(checkout, "rev-parse", "HEAD")
