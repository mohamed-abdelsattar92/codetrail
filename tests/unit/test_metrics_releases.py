"""Releases from the target's tags (design section 19.1)."""

from codetrail.metrics.releases import Releases, measure_releases
from codetrail.repo.history import Tag

DAY = 86_400


def test_releases_are_newest_first_with_the_median_gap() -> None:
    tags = [Tag("v1", "a", 0, ""), Tag("v3", "c", 30 * DAY, "Notes"), Tag("v2", "b", 10 * DAY, "")]
    releases = measure_releases(tags, since_latest=4)
    assert [tag.name for tag in releases.tags] == ["v3", "v2", "v1"]
    assert (releases.since_latest, releases.median_days) == (4, 15)


def test_tags_on_one_date_sort_by_name() -> None:
    assert [tag.name for tag in measure_releases([Tag("a", "x", 0, ""), Tag("b", "x", 0, "")], 0).tags] == ["b", "a"]


def test_fewer_than_two_releases_have_no_median() -> None:
    assert measure_releases([], None) == Releases([], None, None)
    assert measure_releases([Tag("v1", "a", 0, "")], 0).median_days is None
