"""The fake hosting service answers from plain data and records its calls."""

import pytest

from codetrail.hosting import HostedRepository, HostingError, HostingService, HostingStatus, Source
from codetrail.hosting.fake import FakeHosting


def test_the_fake_answers_from_its_data_and_records_calls() -> None:
    repository = HostedRepository(
        service="github",
        source="acme-org",
        id="R_kgDOExample",
        name="web-shop",
        default_branch="main",
        archived=False,
        disabled=False,
        pushed_at=None,
        size_kb=12,
        https_url="https://github.com/acme-org/web-shop.git",
        ssh_url="git@github.com:acme-org/web-shop.git",
        web_url="https://github.com/acme-org/web-shop",
    )
    signed_out = HostingStatus("github", installed=True, signed_in=False, account=None, fix="gh auth login")
    signed_in = HostingStatus("github", installed=True, signed_in=True, account="reader", fix=None)
    fake: HostingService = FakeHosting(
        "github",
        [signed_out, signed_in],
        [Source("github", "acme-org", None)],
        {"acme-org": [repository]},
        {"R_kgDOExample": ["main", "develop"]},
    )
    assert [fake.status(), fake.status(), fake.status()] == [signed_out, signed_in, signed_in]
    assert [source.name for source in fake.sources()] == ["acme-org"]
    assert fake.repositories("acme-org") == [repository]
    assert fake.branches(repository) == ["main", "develop"]
    with pytest.raises(HostingError):
        fake.repositories("other")
    assert isinstance(fake, FakeHosting)
    assert fake.calls[3:] == [
        ("sources", ""),
        ("repositories", "acme-org"),
        ("branches", "R_kgDOExample"),
        ("repositories", "other"),
    ]
