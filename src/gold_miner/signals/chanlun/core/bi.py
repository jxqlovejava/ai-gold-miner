"""笔构建 — 顶底分型交替连接，最小长度约束。"""
from __future__ import annotations

from ..schema import Bi, Fractal


def _purge_fractals(fractals: list[Fractal]) -> list[Fractal]:
    """连续同向分型只保留最极端的一个。"""
    kept: list[Fractal] = []
    for fx in fractals:
        if kept and kept[-1].mark == fx.mark:
            prev = kept[-1]
            if (fx.mark == "G" and fx.fx >= prev.fx) or \
               (fx.mark == "D" and fx.fx <= prev.fx):
                kept[-1] = fx
            continue
        kept.append(fx)
    return kept


def build_bis(fractals: list[Fractal], min_len: int = 4) -> list[Bi]:
    """从分型序列构建笔列表（迭代吸收小波动版，修正贪心折叠 Bug2）。

    迭代规则:
      1. 连续同向分型 → 保留更极端
      2. 相邻异向分型 gap < min_len → 吸收较小波动（保留更极端那侧）
      收敛后连接相邻分型。

    Args:
        fractals: 顶底分型（升序）。
        min_len: 相邻分型最小间隔（去包含K线数），默认 4。

    Returns:
        笔列表，方向严格交替，`macd_area` 初始为 0.0（由 analyzer 回填）。
        注意：周线用迭代版易过度合并（~150 根→2 笔），周线调用方应调大 min_len。
    """
    fs = _purge_fractals(fractals)
    while True:
        merged: list[Fractal] = []
        changed = False
        i = 0
        n = len(fs)
        while i < n:
            if i + 1 >= n:
                merged.append(fs[i])
                break
            a, b = fs[i], fs[i + 1]
            if a.mark == b.mark:
                keep = a if ((a.mark == "G" and a.fx >= b.fx) or
                             (a.mark == "D" and a.fx <= b.fx)) else b
                merged.append(keep)
                changed = True
                i += 2
            elif b.index - a.index < min_len:
                if (a.mark == "G" and a.fx >= b.fx) or \
                   (a.mark == "D" and a.fx <= b.fx):
                    merged.append(a)
                else:
                    merged.append(b)
                changed = True
                i += 2
            else:
                merged.append(a)
                i += 1
        fs = merged
        if not changed:
            break

    bis: list[Bi] = []
    for k in range(len(fs) - 1):
        a, b = fs[k], fs[k + 1]
        if a.mark == b.mark:
            continue
        gap = b.index - a.index
        ok_price = (a.mark == "D" and b.fx > a.fx) or \
                   (a.mark == "G" and b.fx < a.fx)
        if gap >= min_len and ok_price:
            bis.append(Bi(
                direction="up" if a.mark == "D" else "down",
                start_fx=a, end_fx=b,
                high=max(a.fx, b.fx), low=min(a.fx, b.fx),
                length=gap, macd_area=0.0,
                start_dt=a.dt, end_dt=b.dt,
            ))
    return bis
