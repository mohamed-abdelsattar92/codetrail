"""The trend line's points, drawn on the server from numbers only (design section 18.4)."""

import re

from codetrail.web.trend import bars, trend_points


def test_one_value_is_a_flat_line_across() -> None:
    assert trend_points([0.5]) == "2.0,16.0 118.0,16.0"


def test_values_are_scaled_between_the_lowest_and_the_highest() -> None:
    assert trend_points([0.0, 1.0]) == "2.0,30.0 118.0,2.0"
    assert trend_points([3.0, 1.0, 2.0]) == "2.0,2.0 60.0,30.0 118.0,16.0"


def test_equal_values_are_flat_and_no_values_draw_nothing() -> None:
    assert trend_points([4.0, 4.0]) == "2.0,16.0 118.0,16.0"
    assert trend_points([]) == ""


def test_only_numbers_reach_the_svg() -> None:
    assert re.fullmatch(r"[0-9., ]+", trend_points([0.1, 0.7, 0.3, 0.9]))


def test_bars_scale_to_the_highest_value() -> None:
    assert bars([0, 2, 4], width=30, height=10) == [
        (1.0, 10.0, 8.0, 0.0),
        (11.0, 5.0, 8.0, 5.0),
        (21.0, 0.0, 8.0, 10.0),
    ]


def test_no_commits_draw_empty_bars_and_no_values_draw_nothing() -> None:
    assert [bar[3] for bar in bars([0, 0], width=20, height=10)] == [0.0, 0.0]
    assert bars([]) == []
