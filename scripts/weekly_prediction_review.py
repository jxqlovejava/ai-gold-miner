#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""黄金预测自检 · 周复盘 → Hermes cron --no-agent 模式, stdout 投递微信.

分层频率 (2026-08-26): 每次 scan 已内置预测记录+自动结算; 每周由本脚本
输出预测健康卡片 (命中率/方向分布/校准/Brier/警告), 空 stdout 静默。
服务器自主运行, 不依赖用户触发。
"""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from gold_miner.improvement.calibration import build_calibration, calibrate_confidence  # noqa: E402

NEUTRAL_BAND = 0.015  # 与 tracker._NEUTRAL_BAND 一致
JOURNAL = ROOT / "data" / "private" / "prediction_journal.jsonl"
BEIJING = timezone(timedelta(hours=8))


def _is_correct(direction: str, ret: float) -> bool:
    d = (direction or "").lower()
    if d in ("buy", "long", "bullish"):
        return ret > 0
    if d in ("sell", "short", "bearish"):
        return ret < 0
    return abs(ret) < NEUTRAL_BAND


def main() -> int:
    if not JOURNAL.exists():
        return 0
    records: list[dict] = []
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    resolved = [r for r in records if not r.get("invalidated") and r.get("actual_price") is not None]
    if not resolved:
        return 0  # 无已结算 → 空 stdout, Hermes 静默不推送

    n = len(resolved)
    correct = sum(1 for r in resolved if _is_correct(r.get("direction"), r.get("actual_return") or 0))

    dirs: Counter = Counter()
    for r in resolved:
        d = (r.get("direction") or "").lower()
        if d in ("buy", "long", "bullish"):
            dirs["long"] += 1
        elif d in ("sell", "short", "bearish"):
            dirs["short"] += 1
        else:
            dirs["neutral"] += 1

    directional = [r for r in resolved if (r.get("direction") or "").lower() not in ("neutral", "hold")]
    dir_correct = sum(1 for r in directional if _is_correct(r.get("direction"), r.get("actual_return") or 0))

    cal = build_calibration(resolved)
    brier = st.mean(((r.get("confidence") or 0) - (1.0 if _is_correct(r.get("direction"), r.get("actual_return") or 0) else 0.0)) ** 2 for r in resolved)
    brier_cal = st.mean(
        (calibrate_confidence(r.get("direction"), r.get("confidence") or 0, cal)
         - (1.0 if _is_correct(r.get("direction"), r.get("actual_return") or 0) else 0.0)) ** 2
        for r in resolved
    )

    lines = ["📊 黄金预测自检 · 周复盘", ""]
    lines.append(f"✓ 已结算 {n} / 正确 {correct} = **{correct / n:.0%}**")
    if directional:
        lines.append(f"   方向性 {len(directional)} 条 = {dir_correct / len(directional):.0%} (long/short)")
    lines.append(f"   方向分布: {' '.join(f'{k}:{v}' for k, v in dirs.items())}")
    cal_parts = [f"{d} {v['hit_rate']:.0%}(n{int(v['n'])})" for d, v in cal.items()]
    if cal_parts:
        lines.append(f"📐 校准: {' | '.join(cal_parts)}")
    lines.append(f"   Brier: {brier:.3f} → 校准 {brier_cal:.3f}")

    warns = []
    if len(directional) < 30:
        warns.append(f"方向样本仅 {len(directional)} 条")
    neutral_ratio = dirs.get("neutral", 0) / n
    if neutral_ratio > 0.7:
        warns.append(f"观望占比 {neutral_ratio:.0%}")
    if warns:
        lines.append("⚠️ " + "; ".join(warns))

    lines.append("")
    lines.append(f"🤖 {datetime.now(BEIJING).strftime('%m-%d %H:%M')} | 青蚨 · 预测自检")
    print("\n".join(lines), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
