"""Trend lines and activity bars (design sections 18.4 and 19.5): SVG drawn on the server from numbers only."""

from __future__ import annotations

from collections.abc import Sequence

PADDING = 2


def trend_points(values: Sequence[float], width: int = 120, height: int = 32) -> str:
    """The `points` of a polyline, oldest value on the left, the lowest at the bottom; one value draws a flat line."""
    if not values:
        return ""
    low, high = min(values), max(values)

    def y(value: float) -> float:
        middle = 0.5 if high == low else (high - value) / (high - low)
        return PADDING + (height - 2 * PADDING) * middle

    drawn = list(values) if len(values) > 1 else [values[0], values[0]]
    step = (width - 2 * PADDING) / (len(drawn) - 1)
    return " ".join(f"{PADDING + index * step:.1f},{y(value):.1f}" for index, value in enumerate(drawn))


BAR_GAP = 2


def bars(values: Sequence[int], width: int = 480, height: int = 96) -> list[tuple[float, float, float, float]]:
    """Each value's bar as x, y, width and height, scaled to the highest value; a zero has no height."""
    if not values:
        return []
    slot = width / len(values)
    highest = max(values) or 1
    drawn = []
    for index, value in enumerate(values):
        bar_height = height * value / highest
        drawn.append((index * slot + BAR_GAP / 2, height - bar_height, slot - BAR_GAP, bar_height))
    return drawn
