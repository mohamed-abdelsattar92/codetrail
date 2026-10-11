"""The Documentation page's tiles, its sections' helpers and its report cache (design sections 18.4 and 21)."""

from datetime import date

from codetrail.facts import Entity, EntityKind
from codetrail.metrics.report import Metric, Value
from codetrail.repo.history import Commit
from codetrail.web.documentation import (
    ReportCache,
    age,
    build_tiles,
    commit_kind,
    days_proposed,
    filtered,
    section_to_open,
    segments,
)


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


def commit(body: str) -> Commit:
    return Commit("abc1234", "Someone", "2026-10-01", "fix: x", body, [], False)


def test_an_adr_needing_attention_opens_the_decisions() -> None:
    values = {Metric.ADR_ATTENTION: Value(1, None), Metric.DOCUMENTED_SHARE: Value(0, 9)}
    assert section_to_open(values) == "decisions"


def test_otherwise_the_lowest_share_opens() -> None:
    values = {Metric.ADR_ATTENTION: Value(0, None), Metric.DOCUMENTED_SHARE: Value(3, 4),
              Metric.COMMIT_WHY_SHARE: Value(1, 4), Metric.MENTIONED_SHARE: Value(2, 4)}  # fmt: skip
    assert section_to_open(values) == "commits"


def test_a_tie_opens_the_section_higher_on_the_page() -> None:
    values = {Metric.ADR_ATTENTION: Value(0, None), Metric.DOCUMENTED_SHARE: Value(0, 3),
              Metric.COMMIT_WHY_SHARE: Value(0, 2), Metric.MENTIONED_SHARE: Value(0, 2)}  # fmt: skip
    assert section_to_open(values) == "rationale"


def test_what_wasnt_measured_or_is_complete_never_opens() -> None:
    # No pages (nothing to measure), the history unreadable (no commit share), everything mentioned.
    values = {Metric.ADR_ATTENTION: Value(0, None), Metric.DOCUMENTED_SHARE: Value(0, 0),
              Metric.MENTIONED_SHARE: Value(5, 5)}  # fmt: skip
    assert section_to_open(values) is None


def test_a_commits_kind_follows_its_body() -> None:
    assert commit_kind(commit("Just a body.")) == "body-only"
    assert commit_kind(commit("")) == "subject-only"


def test_a_filter_counts_each_kind_and_its_folded_rest() -> None:
    found = filtered(["body-only", "subject-only", "subject-only", "body-only"], listed=2)
    assert found.kinds == ["body-only", "subject-only", "subject-only", "body-only"]
    assert found.totals == {"all": 4, "body-only": 2, "subject-only": 2}
    assert list(found.totals) == ["all", "body-only", "subject-only"]
    assert found.folded == {"all": 2, "body-only": 1, "subject-only": 1}


def test_a_kind_with_nothing_folded_counts_zero() -> None:
    assert filtered(["package", "project"], listed=1).folded == {"all": 1, "package": 0, "project": 1}
    assert filtered([], listed=50).totals == {"all": 0}


def test_segments_share_the_frame_and_leave_out_empty_parts() -> None:
    parts = [("heat-4", 1, "ADRs"), ("heat-3", 0, "Documents"), ("tone-inferred", 3, "Inferred")]
    assert segments(parts) == [("heat-4", 0.0, 250.0), ("tone-inferred", 250.0, 750.0)]
    assert segments([("heat-4", 0, "ADRs")]) == []


def test_the_empty_rest_of_a_share_takes_room_but_isnt_drawn() -> None:
    assert segments([("series-fill-1", 1, ""), ("", 2, "")]) == [("series-fill-1", 0.0, 333.3)]


def test_an_adrs_age_in_days() -> None:
    dated = Entity("decision:ADR-0002", EntityKind.DECISION, {"date": "2026-09-01"})
    assert days_proposed(dated, date(2026, 10, 10)) == 39
    assert days_proposed(Entity("decision:ADR-0003", EntityKind.DECISION, {}), date(2026, 10, 10)) is None
