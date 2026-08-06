"""分型识别 — 顶分型(中间高+高且低+高)/底分型(中间低+低且高+低)。"""
from __future__ import annotations

from ..schema import Fractal
from .merge import MergedBar


def detect_fractals(merged: list[MergedBar]) -> list[Fractal]:
    """在去包含K线上识别顶底分型。

    Args:
        merged: 去包含K线列表（升序）。

    Returns:
        顶底分型列表。平盘（等高/等低）不识别。
    """
    fractals: list[Fractal] = []
    n = len(merged)
    for i in range(1, n - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low:
            fractals.append(Fractal(mark="G", dt=b.dt, high=b.high, low=b.low,
                                    fx=b.high, index=i))
        elif b.high < a.high and b.high < c.high and b.low < a.low and b.low < c.low:
            fractals.append(Fractal(mark="D", dt=b.dt, high=b.high, low=b.low,
                                    fx=b.low, index=i))
    return fractals
