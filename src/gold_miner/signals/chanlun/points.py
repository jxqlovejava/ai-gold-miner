"""买卖点判定 — 一买/二买/三买 + 一卖/二卖/三卖（长多语义）。"""
from __future__ import annotations

from .schema import Bi, ChanlunPoint, ZhongShu


def detect_points(bis: list[Bi], zss: list[ZhongShu],
                  divergences: dict[int, dict]) -> list[ChanlunPoint]:
    """从笔/中枢/背驰推导买卖点。

    - 一买：下降趋势（有中枢）+ 末段底背驰 + 底分型确认
    - 二买：一买后回调低点不破一买低点
    - 三买：突破中枢 ZG 后回抽低点 > ZG（不进入中枢）
    - 一卖/二卖/三卖：镜像
    """
    points: list[ChanlunPoint] = []
    n = len(bis)

    def add(kind: str, bi: Bi, price: float, conf: float, reason: str) -> None:
        points.append(ChanlunPoint(kind=kind, dt=bi.end_dt, price=price,
                                   confidence=conf, rationale=reason))

    first_idx: dict[str, int] = {}
    for idx, d in divergences.items():
        if idx >= n:
            continue
        b = bis[idx]
        if d["type"] == "bottom" and zss:
            add("一买", b, b.low, 0.7, "下降末段底背驰+底分型确认(有中枢趋势背景)")
            first_idx["一买"] = idx
        elif d["type"] == "top" and zss:
            add("一卖", b, b.high, 0.7, "上升末段顶背驰+顶分型确认(有中枢趋势背景)")
            first_idx["一卖"] = idx

    if "一买" in first_idx:
        base = bis[first_idx["一买"]].low
        for j in range(first_idx["一买"] + 1, n):
            b = bis[j]
            if b.direction == "down" and b.low > base:
                add("二买", b, b.low, 0.75, "一买后回调不破一买低点, 底分型确认")
                break
    if "一卖" in first_idx:
        base = bis[first_idx["一卖"]].high
        for j in range(first_idx["一卖"] + 1, n):
            b = bis[j]
            if b.direction == "up" and b.high < base:
                add("二卖", b, b.high, 0.75, "一卖后反弹不破一卖高点, 顶分型确认")
                break

    for zs in zss[-2:]:                  # 只看最近 2 个中枢（当前结构相关，避免远古中枢误触发）
        last_bi = max(zs.bi_indexes) if zs.bi_indexes else 0
        for j in range(last_bi + 1, n):
            b = bis[j]
            if b.direction == "down" and b.low > zs.zg:
                add("三买", b, b.low, 0.8, f"突破中枢ZG={zs.zg:.2f}后回抽不进入中枢")
            elif b.direction == "up" and b.high < zs.zd:
                add("三卖", b, b.high, 0.8, f"跌破中枢ZD={zs.zd:.2f}后反弹不进入中枢")
    return points
