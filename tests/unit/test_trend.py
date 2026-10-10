"""The trend line's points, drawn on the server from numbers only (design section 18.4)."""

import re

from codetrail.web.trend import trend_points


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
