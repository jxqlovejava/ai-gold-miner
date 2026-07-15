#!/usr/bin/env python3
"""日历事件日期+钟点校验 — 每次分析前自动运行。

拦截:
  1. 周末/错误星期 (DOW)
  2. **钟点双重换算** (北京钟点误当美东写入) — 2026-07-15 沃什听证事故
  3. 宏观数据非 08:30 一带的异常钟点

规则来源：
  - BLS 官方发布日历 (bls.gov/schedule) — 通常 08:30 ET
  - 国会听证 notice — 通常 10:00 ET
  - DOL 周度初请失业金 (每周四)

时区约定：
  - scheduled_at **只存美东墙上钟点** + 偏移 (-04:00 EDT / -05:00 EST)
  - 展示必须同时输出 ET | 北京 两列 (见 dual_clock_str)
  - 禁止: 先换算北京 → 再把北京小时数写回 scheduled_at

写入前强制 checklist (与 CLAUDE.md 第〇步一致):
  [ ] 官网/notice 原文钟点是哪个时区?
  [ ] 已用 make_et_iso / 美东本地钟点写入?
  [ ] 校验打印的「ET | 北京」两列都合理?
  [ ] 国会听证不应出现 ET 晚上 18:00+ (那是北京次日上午的典型错误形态)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 允许直接 `python scripts/validate_calendar_dates.py` (无安装包时)
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gold_miner.data.calendar_time_rules import (  # noqa: E402
    check_event_clock,
    dual_clock_str,
)

CALENDAR_FILE = _ROOT / "data" / "calendar_events.jsonl"

# 事件类型 → 允许的星期 (0=Mon, 6=Sun) — 基于美东日期
DOW_RULES = {
    "nfp": {3},
    "cpi": {1, 2, 3, 4},
    "ppi": {0, 1, 2, 3, 4},
    "pce": {1, 2, 3, 4},
    "fomc_minutes": {2, 3},
    "fed_rate": {2, 3},
    "pmi": {0, 1, 2, 3, 4},
    "fed_speech": {0, 1, 2, 3, 4},
    "geo": {0, 1, 2, 3, 4, 5, 6},
    "monitor": {0, 1, 2, 3, 4, 5, 6},
}

NAME_DOW_OVERRIDES = {
    "初请失业金人数": {3},
}

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_BEIJING_TZ = timezone(timedelta(hours=8))


def _to_beijing(et_dt: datetime) -> datetime:
    if et_dt.tzinfo is None:
        return et_dt.replace(tzinfo=timezone.utc).astimezone(_BEIJING_TZ)
    return et_dt.astimezone(_BEIJING_TZ)


def validate() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not CALENDAR_FILE.exists():
        return errors, warnings

    with open(CALENDAR_FILE, encoding="utf-8") as f:
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

        # 无时区偏移 → 旧格式
        if dt.tzinfo is None:
            warnings.append(
                f"旧格式(无时区): [{etype}] {name} → {scheduled} (应加 -04:00/-05:00)"
            )

        dow = dt.weekday()
        dow_name = DAYS[dow]
        dual = dual_clock_str(dt) if dt.tzinfo else f"naive {dt}"

        # 周末检查 (非 geo/monitor)
        if dow >= 5 and etype not in ("geo", "monitor"):
            errors.append(
                f"周末事件(ET): [{etype}] {name} → {dual} | ET星期 {dow_name}"
            )
            continue

        # DOW 规则
        allowed = NAME_DOW_OVERRIDES.get(name) or DOW_RULES.get(etype)
        if allowed is not None and dow not in allowed:
            allowed_names = ", ".join(DAYS[d] for d in sorted(allowed))
            warnings.append(
                f"星期异常(ET): [{etype}] {name} → {dual} | ET星期 {dow_name} "
                f"| 该类型通常允许: {allowed_names}"
            )

        # 钟点 / 双重换算硬规则
        for finding in check_event_clock(
            name=name, event_type=etype, scheduled_at=dt
        ):
            if finding.severity == "error":
                errors.append(finding.message)
            else:
                warnings.append(finding.message)

    return errors, warnings


if __name__ == "__main__":
    errors, warnings = validate()
    if errors:
        print("🔴 日期/钟点校验失败 — 以下事件必须修复 (禁止带错时继续分析):")
        for e in errors:
            print(f"  ❌ {e}")
    if warnings:
        print("🟡 日期/钟点校验警告 — 请人工确认:")
        for w in warnings:
            print(f"  ⚠️  {w}")
    if not errors and not warnings:
        print("✅ 日历日期+钟点校验通过")

    if errors:
        sys.exit(1)
    sys.exit(0)
