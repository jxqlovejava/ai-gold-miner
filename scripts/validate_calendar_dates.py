#!/usr/bin/env python3
"""日历事件日期校验 — 每次分析前自动运行，拦截周末/错误星期的事件日期。

规则来源：
  - BLS 官方发布日历 (bls.gov/schedule)
  - DOL 周度初请失业金 (每周四)
  - 常识：CPI/PPI/PCE 不排在周五/周末，ISM PMI 在每月第1-2个工作日
"""

import json
import sys
from datetime import datetime
from pathlib import Path

CALENDAR_FILE = Path(__file__).resolve().parent.parent / "data" / "calendar_events.jsonl"

# 事件类型 → 允许的星期 (0=Mon, 6=Sun)
DOW_RULES = {
    "nfp": {3},           # 初请失业金: 每周四 (非农除外，非农通常周四/五)
    "cpi": {1, 2, 3, 4},  # CPI: 周二-周五 (BLS 2026: 2/13 Fri, 4/10 Fri, 9/11 Fri)
    "ppi": {0, 1, 2, 3, 4},  # PPI: 工作日 (CPI周五→PPI周一)
    "pce": {1, 2, 3, 4},  # PCE: 周二-周五 (BEA月末工作日)
    "fomc_minutes": {2, 3}, # FOMC纪要: 周三-周四 (通常周三)
    "fed_rate": {2, 3},   # FOMC决议: 周三-周四
    "pmi": {0, 1, 2, 3, 4}, # ISM PMI: 工作日即可
    "fed_speech": {0, 1, 2, 3, 4}, # 讲话: 工作日
    "geo": {0, 1, 2, 3, 4, 5, 6}, # 地缘: 任意
    "monitor": {0, 1, 2, 3, 4, 5, 6}, # 内部: 任意
}

# 特定事件名称 → DOW 覆盖
NAME_DOW_OVERRIDES = {
    "初请失业金人数": {3},  # 永远是周四
}

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def validate() -> tuple[list[str], list[str]]:
    errors = []
    warnings = []

    if not CALENDAR_FILE.exists():
        return errors, warnings

    with open(CALENDAR_FILE) as f:
        events = [json.loads(line) for line in f if line.strip()]

    for e in events:
        name = e.get("name", "")
        etype = e.get("event_type", "")
        scheduled = e.get("scheduled_at", "")
        if not scheduled:
            continue

        try:
            dt = datetime.fromisoformat(scheduled)
        except ValueError:
            errors.append(f"无法解析日期: {name} → {scheduled}")
            continue

        dow = dt.weekday()  # 0=Mon
        dow_name = DAYS[dow]

        # 周末检查 (非 geo/monitor)
        if dow >= 5 and etype not in ("geo", "monitor"):
            errors.append(
                f"周末事件: [{etype}] {name} → {dt.date()} ({dow_name})"
            )
            continue

        # DOW 规则检查
        allowed = NAME_DOW_OVERRIDES.get(name) or DOW_RULES.get(etype)
        if allowed is not None and dow not in allowed:
            allowed_names = ", ".join(DAYS[d] for d in sorted(allowed))
            warnings.append(
                f"星期异常: [{etype}] {name} → {dt.date()} ({dow_name}), "
                f"该类型通常允许: {allowed_names}"
            )

    return errors, warnings


if __name__ == "__main__":
    errors, warnings = validate()
    if errors:
        print("🔴 日期校验失败 — 以下事件必须修复:")
        for e in errors:
            print(f"  ❌ {e}")
    if warnings:
        print("🟡 日期校验警告 — 请人工确认:")
        for w in warnings:
            print(f"  ⚠️  {w}")
    if not errors and not warnings:
        print("✅ 日历日期校验通过")

    # 周末错误是硬拦截
    if errors:
        sys.exit(1)
    sys.exit(0)
