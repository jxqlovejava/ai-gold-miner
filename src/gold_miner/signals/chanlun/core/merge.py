"""去包含处理 — 缠论新K线合并。上升取较大高/较大低，下降取较小高/较小低。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MergedBar:
    """去包含后的合并K线。direction: ""(首根)/"up"/"down"。"""

    index: int        # 原始 DataFrame 位置（末根合并进该根的 index）
    dt: Any
    high: float
    low: float
    direction: str


def merge_bars(df) -> list[MergedBar]:
    """将 OHLCV DataFrame 合并为去包含K线列表。

    Args:
        df: 含 open/high/low/close 列，index=datetime。

    Returns:
        升序 MergedBar 列表；空输入返回 []。
    """
    if df is None or len(df) == 0:
        return []
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    dts = df.index

    merged = [MergedBar(0, dts[0], highs[0], lows[0], direction="")]
    for i in range(1, len(df)):
        prev = merged[-1]
        hi, lo = highs[i], lows[i]
        contains = (hi >= prev.high and lo <= prev.low) or \
                   (prev.high >= hi and prev.low <= lo)
        if contains:
            if prev.direction == "up":
                new_hi, new_lo, direction = max(hi, prev.high), max(lo, prev.low), "up"
            elif prev.direction == "down":
                new_hi, new_lo, direction = min(hi, prev.high), min(lo, prev.low), "down"
            else:  # 首根被包含，方向按 high 关系判定
                direction = "up" if hi >= prev.high else "down"
                if direction == "up":
                    new_hi, new_lo = max(hi, prev.high), max(lo, prev.low)
                else:
                    new_hi, new_lo = min(hi, prev.high), min(lo, prev.low)
            merged[-1] = MergedBar(i, dts[i], new_hi, new_lo, direction)
        else:
            direction = "up" if hi > prev.high else "down"
            merged.append(MergedBar(i, dts[i], hi, lo, direction))
    return merged
