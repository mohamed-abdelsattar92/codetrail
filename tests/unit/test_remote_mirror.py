"""Mirrors of remote targets: fetched over https or ssh with the reader's own git sign-in (design section 24.4)."""

import time
from pathlib import Path

import pytest

from codetrail.config import TargetConfig
from codetrail.errors import CodetrailError
from codetrail.repo.git import run_git
from codetrail.repo.remote import (
    REMOTE_PROTOCOLS,
    fetch_target,
    refresh_remote_mirror,
    remote_git_environment,
    remote_options,
)
from tests.fixtures.remote import (
    SignInRequiredHandler,
    hermetic_home,
    publish,
    serving,
    serving_repository,
    stalling_server,
    within,
)
from tests.fixtures.repos import add_commit, git, make_repository

HTTP_ONLY = ("http",)
STALL_SECONDS = 2
FIX = "gh auth setup-git"


@pytest.fixture(autouse=True)
def reader_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The reader's home, empty: the developer's real git configuration and credential helpers aren't consulted."""
    return hermetic_home(monkeypatch, tmp_path / "reader-home")


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
        "https_proxy": "http://proxy.example:3128",
        "NO_PROXY": "internal.example",
        "LC_ALL": "C",
        "GH_TOKEN": "x",
        "GITHUB_TOKEN": "x",
        "AZURE_DEVOPS_EXT_PAT": "x",
        "GITLAB_TOKEN": "x",
        "GIT_ASKPASS": "/bin/steal",
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_CONFIG_COUNT": "1",
        "AWS_SECRET_ACCESS_KEY": "x",
    }
    environment = remote_git_environment(environ, STALL_SECONDS)
    assert environment["SSH_AUTH_SOCK"] == "/run/agent.sock"
    assert environment["GIT_SSL_CAINFO"] == "/etc/corporate.pem"
    assert environment["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert environment["HOME"] == "/home/reader"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CEILING_DIRECTORIES"] == "/"
    assert environment["https_proxy"] == "http://proxy.example:3128"
    assert environment["NO_PROXY"] == "internal.example"
    assert environment["LC_ALL"] == "C"
    assert environment["GIT_SSH_COMMAND"] == (
        "ssh -o BatchMode=yes -o ConnectTimeout=2 -o ServerAliveInterval=2 -o ServerAliveCountMax=1"
    )
    kept = {"PATH", "HOME", "SSH_AUTH_SOCK", "GIT_SSL_CAINFO", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "LC_ALL"}
    assert set(environment) - kept == {
        "GIT_TERMINAL_PROMPT",
        "GIT_CEILING_DIRECTORIES",
        "GIT_SSH_COMMAND",
    }


def test_the_remote_options_allow_only_the_named_protocols_and_never_ask() -> None:
    assert remote_options(REMOTE_PROTOCOLS, STALL_SECONDS) == [
        "-c", "protocol.allow=never", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never",
        "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always",
        "-c", "core.askPass=", "-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=2",
    ]  # fmt: skip


def test_file_ext_and_http_are_refused_by_default(checkout: Path, tmp_path: Path, reader_home: Path) -> None:
    # The reader's own git configuration allows all three, and each URL would work if it were allowed.
    (reader_home / ".gitconfig").write_text("[protocol.file]\n\tallow = always\n[protocol.ext]\n\tallow = always\n")
    marker = tmp_path / "marker"
    with serving_repository(checkout, tmp_path / "served") as http_url:
        for url in (http_url, checkout.as_uri(), f"ext::touch {marker}"):
            mirror = tmp_path / "mirror.git"
            with pytest.raises(CodetrailError, match="couldn't fetch"):
                refresh_remote_mirror(mirror, url, "develop", sign_in_fix=FIX, stall_seconds=STALL_SECONDS)
            assert not mirror.exists()
    assert not marker.exists()


def test_a_remote_mirror_clones_and_fetches(checkout: Path, tmp_path: Path) -> None:
    mirror = tmp_path / "data" / "mirror.git"
    git(checkout, "tag", "-a", "v1", "-m", "First")
    git(checkout, "tag", "old")
    with serving_repository(checkout, tmp_path / "served") as url:
        first = refresh_remote_mirror(
            mirror, url, "develop", sign_in_fix=FIX, protocols=HTTP_ONLY, stall_seconds=STALL_SECONDS
        )
        assert first == git(checkout, "rev-parse", "HEAD")
        assert tags_in(mirror) == ["old", "v1"]
        second_commit = add_commit(checkout, {"app/extra.py": "x = 1\n"})
        git(checkout, "tag", "-d", "old")
        git(checkout, "tag", "v2")
        publish(checkout, tmp_path / "served")
        second = refresh_remote_mirror(
            mirror, url, "develop", sign_in_fix=FIX, protocols=HTTP_ONLY, stall_seconds=STALL_SECONDS
        )
    assert second == second_commit
    assert tags_in(mirror) == ["v1", "v2"]


def test_a_missing_branch_is_named(checkout: Path, tmp_path: Path) -> None:
    with (
        serving_repository(checkout, tmp_path / "served") as url,
        pytest.raises(CodetrailError, match="no branch 'main'"),
    ):
        refresh_remote_mirror(
            tmp_path / "mirror.git", url, "main", sign_in_fix=FIX, protocols=HTTP_ONLY, stall_seconds=STALL_SECONDS
        )


def fetch_from(base_url: str, mirror: Path) -> str:
    return refresh_remote_mirror(
        mirror, f"{base_url}/x.git", "develop", sign_in_fix=FIX, protocols=HTTP_ONLY, stall_seconds=STALL_SECONDS
    )


def test_a_fetch_that_needs_a_sign_in_names_the_fix(tmp_path: Path) -> None:
    with serving(SignInRequiredHandler) as base_url, pytest.raises(CodetrailError) as raised:
        within(30, lambda: fetch_from(base_url, tmp_path / "mirror.git"))  # git never prompts, so it doesn't wait
    message = str(raised.value)
    assert FIX in message
    assert "@" not in message
    assert "error:" not in message and "Authentication" not in message  # git's own words are never shown


def test_a_server_that_stalls_fails_the_fetch(tmp_path: Path) -> None:
    started = time.monotonic()
    with stalling_server() as base_url, pytest.raises(CodetrailError, match="couldn't fetch"):
        within(30, lambda: fetch_from(base_url, tmp_path / "mirror.git"))
    assert time.monotonic() - started < 10


def test_fetch_target_checks_the_url_again_and_names_the_services_fix(tmp_path: Path) -> None:
    remote = {"service": "github", "source": "acme-org", "id": "R_1", "name": "web-shop",
              "url": "https://github.com/acme-org/web-shop.git"}  # fmt: skip
    target = TargetConfig(remote=remote, branch="main")  # type: ignore[arg-type]
    object.__setattr__(target.remote, "url", "https://example.com/acme-org/web-shop.git")
    with pytest.raises(CodetrailError, match="github"):
        fetch_target(tmp_path / "mirror.git", target, stall_seconds=STALL_SECONDS)
    assert not (tmp_path / "mirror.git").exists()


def test_fetch_target_reads_a_local_target_as_before(checkout: Path, tmp_path: Path) -> None:
    target = TargetConfig(repository=checkout, branch="develop")
    assert fetch_target(tmp_path / "mirror.git", target, stall_seconds=STALL_SECONDS) == git(
        checkout, "rev-parse", "HEAD"
    )
