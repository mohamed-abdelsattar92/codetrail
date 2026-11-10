"""Which of a source's repositories a team follows (design section 24.5)."""

from datetime import UTC, datetime, timedelta

from codetrail.config import TeamSource
from codetrail.hosting import HostedRepository
from codetrail.team.follow import followed

NOW = datetime(2026, 11, 10, tzinfo=UTC)


def repository(
    identifier: str,
    days_since_push: int | None = 1,
    archived: bool = False,
    disabled: bool = False,
    default_branch: str | None = "main",
) -> HostedRepository:
    return HostedRepository(
        service="github",
        source="acme-org",
        id=identifier,
        name=f"repo-{identifier}",
        default_branch=default_branch,
        archived=archived,
        disabled=disabled,
        pushed_at=None if days_since_push is None else NOW - timedelta(days=days_since_push),
        size_kb=1,
        https_url=f"https://github.com/acme-org/repo-{identifier}.git",
        ssh_url=f"git@github.com:acme-org/repo-{identifier}.git",
        web_url=f"https://github.com/acme-org/repo-{identifier}",
    )


def ids(repositories: list[HostedRepository]) -> list[str]:
    return [found.id for found in repositories]


def follow_all(**changes: object) -> TeamSource:
    return TeamSource(service="github", source="acme-org", **changes)  # type: ignore[arg-type]


def test_follow_all_takes_active_repositories_only() -> None:
    listed = [repository(f"r{number}", days_since_push=1 if number < 40 else 400) for number in range(600)]

    chosen = followed(follow_all(), listed, set(), NOW, 90)

    assert len(chosen) == 40


def test_archived_disabled_and_empty_are_never_followed() -> None:
    listed = [
        repository("archived", archived=True),
        repository("disabled", disabled=True),
        repository("empty", default_branch=None),
        repository("fine"),
    ]

    assert ids(followed(follow_all(), listed, {"archived", "disabled", "empty"}, NOW, 90)) == ["fine"]


def test_forks_and_templates_count() -> None:
    # The service's fork and template flags aren't read, so a fork is listed like any repository and is followed.
    assert ids(followed(follow_all(), [repository("a-fork")], set(), NOW, 90)) == ["a-fork"]


def test_a_repository_with_no_push_time_counts_as_active() -> None:
    assert ids(followed(follow_all(), [repository("azure", days_since_push=None)], set(), NOW, 90)) == ["azure"]


def test_a_quiet_followed_repository_stays_followed() -> None:
    listed = [repository("quiet", days_since_push=400), repository("other", days_since_push=400)]

    assert ids(followed(follow_all(), listed, {"quiet"}, NOW, 90)) == ["quiet"]


def test_left_out_ids_are_not_followed() -> None:
    listed = [repository("left"), repository("kept")]

    assert ids(followed(follow_all(left_out=["left"]), listed, {"left"}, NOW, 90)) == ["kept"]


def test_chosen_follows_exactly_the_chosen() -> None:
    listed = [repository("a"), repository("b", days_since_push=400), repository("c")]
    source = follow_all(follow="chosen", chosen=["b", "gone"])

    assert ids(followed(source, listed, set(), NOW, 90)) == ["b"]


def test_chosen_never_follows_archived_disabled_or_empty() -> None:
    listed = [
        repository("archived", archived=True),
        repository("disabled", disabled=True),
        repository("empty", default_branch=None),
        repository("fine"),
    ]
    source = follow_all(follow="chosen", chosen=["archived", "disabled", "empty", "fine"])

    assert ids(followed(source, listed, set(), NOW, 90)) == ["fine"]


def test_ids_that_could_be_flags_are_refused() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        follow_all(left_out=["-x"])
    with pytest.raises(ValidationError):
        follow_all(follow="chosen", chosen=["a b"])
