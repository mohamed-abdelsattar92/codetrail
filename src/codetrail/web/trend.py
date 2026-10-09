"""The documentation metrics' trend lines (design section 18.4): SVG points drawn on the server from numbers only."""

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
