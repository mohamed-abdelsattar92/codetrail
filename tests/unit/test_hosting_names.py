"""Names and clone URLs the hosting services accept (design section 24.3)."""

import pytest

from codetrail.errors import CodetrailError
from codetrail.hosting import HostedRepository
from codetrail.hosting.names import (
    SERVICE_NAMES,
    Service,
    check_clone_url,
    validate_repository_name,
    validate_source,
)

SERVICES: list[Service] = ["github", "azure_devops", "gitlab"]
ACCEPTED_SOURCES = {
    "github": ["acme-org", "a"],
    "azure_devops": ["contoso/Platform", "contoso/Payments API 2"],
    "gitlab": ["example-group", "example-group/sub.group/web-shop"],
}
REFUSED_SOURCES = {
    "github": ["..", "-acme", "acme\n"],
    "azure_devops": [
        "contoso/..",
        "-contoso/Platform",
        "contoso/Plat\x07form",
        "contoso/-x",
        "contoso",
        "contoso/Platform/x",
        "contoso/Plat\udfffform",  # a lone surrogate can't be written to the team file as UTF-8
        "contoso/Plat\ue000form",  # private use
    ],
    "gitlab": ["example-group/../x", "-example", "example\x00group"],
}
ACCEPTED_REPOSITORIES = {
    "github": ["web-shop", "web.shop_2"],
    "azure_devops": ["Payments API 2", "web-shop"],
    "gitlab": ["web-shop", "web.shop"],
}
REFUSED_REPOSITORIES = {
    "github": ["..", "-web-shop", "web\nshop"],
    "azure_devops": [
        ".hidden",
        "-web-shop",
        "web\u202eshop",
        "web\x1fshop",
        "a#b",
        "ends.",
        "web\ud800shop",
        "web\U000f0000shop",
        "web\u2028shop",  # line separator
        "web\u2029shop",  # paragraph separator
        "web\U000e0080shop",  # unassigned
    ],
    "gitlab": ["..", "-web-shop", "web\x00shop"],
}


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_accepts_and_refuses_names(service: Service) -> None:
    for name in ACCEPTED_SOURCES[service]:
        assert validate_source(service, name) == name
    for name in ACCEPTED_REPOSITORIES[service]:
        assert validate_repository_name(service, name) == name
    for name in REFUSED_SOURCES[service]:
        with pytest.raises(CodetrailError):
            validate_source(service, name)
    for name in REFUSED_REPOSITORIES[service]:
        with pytest.raises(CodetrailError):
            validate_repository_name(service, name)


def test_a_gitlab_path_has_at_most_twenty_segments() -> None:
    assert validate_source("gitlab", "/".join(["a"] * 20))
    with pytest.raises(CodetrailError):
        validate_source("gitlab", "/".join(["a"] * 21))


@pytest.mark.parametrize(
    ("service", "url"),
    [
        ("github", "file:///tmp/repo"),
        ("github", "ext::sh -c x"),
        ("github", "http://github.com/acme-org/web-shop.git"),
        ("github", "https://github.com.evil.example/acme-org/web-shop.git"),
        ("github", "https://user:pass@github.com/acme-org/web-shop.git"),
        ("github", "https://github.com/acme-org/web-shop.git#fragment"),
        ("github", "git@github.com:-oProxyCommand=x"),
        ("azure_devops", "https://a:b@dev.azure.com/a/p/_git/r"),
    ],
)
def test_clone_urls_must_be_https_or_ssh_on_the_service_host(service: Service, url: str) -> None:
    with pytest.raises(CodetrailError, match="clone URL"):
        check_clone_url(service, url)


def test_a_repository_gives_the_clone_url_the_reader_chose() -> None:
    repository = HostedRepository(
        service="github",
        source="acme-org",
        id="R_kgDOExample",
        name="web-shop",
        default_branch="main",
        archived=False,
        disabled=False,
        pushed_at=None,
        size_kb=None,
        https_url="https://github.com/acme-org/web-shop.git",
        ssh_url="git@github.com:acme-org/web-shop.git",
        web_url="https://github.com/acme-org/web-shop",
    )
    assert repository.clone_url("https") == repository.https_url
    assert repository.clone_url("ssh") == repository.ssh_url


@pytest.mark.parametrize(
    ("service", "url", "shape"),
    [
        ("github", "https://reader:secret@github.com/acme-org/web-shop.git", "git@github.com:"),
        ("azure_devops", "https://contoso:secret@dev.azure.com/contoso/Platform/_git/r", "git@ssh.dev.azure.com:"),
        ("gitlab", "https://reader:secret@gitlab.example/example-group/web-shop.git", "https://gitlab.com/"),
    ],
)
def test_a_refused_clone_url_is_not_echoed(service: Service, url: str, shape: str) -> None:
    # The URL may carry a password or token, and the message reaches the terminal and the page.
    with pytest.raises(CodetrailError) as raised:
        check_clone_url(service, url)
    message = str(raised.value)
    assert "secret" not in message and "reader" not in message
    assert SERVICE_NAMES[service] in message and shape in message
