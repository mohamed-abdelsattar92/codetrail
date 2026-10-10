"""The Documentation page's tiles and its report cache (design section 18.4)."""

from datetime import date

from codetrail.metrics.report import Metric, Value
from codetrail.web.documentation import ReportCache, age, build_tiles


def test_a_share_shows_its_percent_change_and_trend() -> None:
    tiles = build_tiles(
        {Metric.DOCUMENTED_SHARE: Value(2, 4), Metric.ADR_ATTENTION: Value(3, None)},
        {Metric.DOCUMENTED_SHARE: Value(1, 3), Metric.ADR_ATTENTION: Value(1, None)},
        {Metric.DOCUMENTED_SHARE: [Value(1, 3), Value(0, 0), Value(2, 4)], Metric.ADR_ATTENTION: [Value(1, None)]},
    )
    share, count = tiles[Metric.DOCUMENTED_SHARE], tiles[Metric.ADR_ATTENTION]
    assert (share.percent, share.change, share.trend_text) == (50, 17, "33%, 50%")  # nothing measured: no point
    assert share.points.count(",") == 2
    assert (count.percent, count.change, count.trend_text) == (None, 2, "1")


def test_a_share_shows_100_and_0_percent_only_when_it_is_exactly_that() -> None:
    tiles = build_tiles(
        {Metric.COMMIT_WHY_SHARE: Value(199, 200), Metric.MENTIONED_SHARE: Value(1, 1000)},
        {},
        {Metric.COMMIT_WHY_SHARE: [Value(199, 200), Value(200, 200)], Metric.MENTIONED_SHARE: [Value(0, 3)]},
    )
    assert (tiles[Metric.COMMIT_WHY_SHARE].percent, tiles[Metric.COMMIT_WHY_SHARE].trend_text) == (99, "99%, 100%")
    assert (tiles[Metric.MENTIONED_SHARE].percent, tiles[Metric.MENTIONED_SHARE].trend_text) == (1, "0%")


def test_a_metric_with_nothing_before_or_now_has_no_change() -> None:
    tiles = build_tiles({Metric.DOCUMENTED_SHARE: Value(0, 0)}, {}, {})
    assert (tiles[Metric.DOCUMENTED_SHARE].percent, tiles[Metric.DOCUMENTED_SHARE].change) == (None, None)
    assert tiles[Metric.COMMIT_WHY_SHARE].value is None  # not measured: the history couldn't be read


def test_the_cache_builds_once_per_key() -> None:
    cache: ReportCache[str] = ReportCache()
    built: list[str] = []

    def build(text: str) -> str:
        built.append(text)
        return text

    assert cache.get(("snapshot 1", "head a"), lambda: build("first")) == "first"
    assert cache.get(("snapshot 1", "head a"), lambda: build("again")) == "first"
    assert cache.get(("snapshot 2", "head a"), lambda: build("second")) == "second"
    assert built == ["first", "second"]


def test_age_counts_whole_months_in_years_and_months() -> None:
    assert age(date(2024, 1, 31), date(2026, 10, 10)) == (2, 8)
    assert age(date(2024, 1, 10), date(2026, 10, 10)) == (2, 9)
    assert age(date(2026, 10, 10), date(2026, 10, 10)) == (0, 0)
    assert age(date(2026, 10, 11), date(2026, 10, 10)) == (0, 0)  # a clock behind the commit's date


def test_the_new_counts_show_their_difference_not_points() -> None:
    tiles = build_tiles({Metric.CODE_LINES: Value(120, None)}, {Metric.CODE_LINES: Value(100, None)}, {})
    assert (tiles[Metric.CODE_LINES].percent, tiles[Metric.CODE_LINES].change) == (None, 20)
