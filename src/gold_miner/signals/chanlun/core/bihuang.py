"""背驰判定 — 相邻同向笔力度（MACD 面积）比较。"""
from __future__ import annotations

from ..schema import Bi


def detect_divergence(bis: list[Bi]) -> dict[int, dict]:
    """检测背驰。

    底背驰：下降笔创更低低点但 MACD 面积较前一段下降笔减小。
    顶背驰：上升笔创更高高点但 MACD 面积较前一段上升笔减小。

    Args:
        bis: 笔列表（含 macd_area）。

    Returns:
        {笔 index: {"type": "bottom"/"top", "bi_index": index}}。
    """
    div: dict[int, dict] = {}
    for i in range(2, len(bis)):
        b, prev = bis[i], bis[i - 2]
        if (b.direction == "down" and prev.direction == "down"
                and b.low < prev.low and b.macd_area < prev.macd_area):
            div[i] = {"type": "bottom", "bi_index": i}
        elif (b.direction == "up" and prev.direction == "up"
              and b.high > prev.high and b.macd_area < prev.macd_area):
            div[i] = {"type": "top", "bi_index": i}
    return div
