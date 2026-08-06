"""中枢识别 — ≥3 笔重叠区间，延伸合并与上移/下移判定。"""
from __future__ import annotations

from dataclasses import replace

from ..schema import Bi, ZhongShu


def _overlap(z1: ZhongShu, z2: ZhongShu) -> bool:
    return z1.zg > z2.zd and z2.zg > z1.zd


def detect_zhongshus(bis: list[Bi]) -> list[ZhongShu]:
    """从笔序列识别中枢。

    Args:
        bis: 笔列表（升序）。

    Returns:
        中枢列表（升序），state 为 "形成"/"延伸"/"上移"/"下移"。
    """
    n = len(bis)
    raw: list[ZhongShu] = []
    i = 0
    while i <= n - 3:
        three = bis[i:i + 3]
        zg = min(b.high for b in three)
        zd = max(b.low for b in three)
        if zg > zd:
            raw.append(ZhongShu(
                zg=zg, zd=zd, zz=(zg + zd) / 2.0,
                gg=max(b.high for b in three),
                dd=min(b.low for b in three),
                start_dt=three[0].start_dt, end_dt=three[2].end_dt,
                state="形成", bi_indexes=tuple(range(i, i + 3)),
            ))
            i += 3
        else:
            i += 1

    # 相邻中枢重叠 → 延伸合并
    merged: list[ZhongShu] = []
    for zs in raw:
        if merged and _overlap(merged[-1], zs):
            prev = merged[-1]
            zg = min(prev.zg, zs.zg)
            zd = max(prev.zd, zs.zd)
            merged[-1] = replace(
                prev, zg=zg, zd=zd, zz=(zg + zd) / 2.0,
                gg=max(prev.gg, zs.gg), dd=min(prev.dd, zs.dd),
                end_dt=zs.end_dt, state="延伸",
                bi_indexes=prev.bi_indexes + zs.bi_indexes,
            )
        else:
            merged.append(zs)

    # 上移/下移状态
    for k in range(1, len(merged)):
        if merged[k].zd > merged[k - 1].zg:
            merged[k] = replace(merged[k], state="上移")
        elif merged[k].zg < merged[k - 1].zd:
            merged[k] = replace(merged[k], state="下移")
    return merged
