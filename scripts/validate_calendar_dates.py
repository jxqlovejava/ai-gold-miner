#!/usr/bin/env python3
"""日历事件日期+钟点+DOW 三重校验 — 每次分析前自动运行。

拦截:
  1. 周末/错误星期 (DOW) — 防止「周三初请失业金」类输出错误
  2. **钟点双重换算** (北京钟点误当美东写入) — 2026-07-15 沃什听证事故
  3. 宏观数据非 08:30 一带的异常钟点

规则来源：
  - BLS 官方发布日历 (bls.gov/schedule) — 通常 08:30 ET
  - 国会听证 notice — 通常 10:00 ET
  - DOL 周度初请失业金 (每周四)
  - Federal Reserve FOMC 日程

时区约定：
  - scheduled_at **只存美东墙上钟点** + 偏移 (-04:00 EDT / -05:00 EST)
  - 展示必须同时输出 ET | 北京 两列 (见 dual_clock_str)
  - 禁止: 先换算北京 → 再把北京小时数写回 scheduled_at

模式:
  - 默认: 检查所有事件, 输出 errors + warnings
  - --strict: warnings 也导致 exit(1)
  - --ref-table N: 输出未来 N 天的 DOW 参考表 (Markdown), 供分析报告引用

写入前强制 checklist (与 CLAUDE.md 第〇步一致):
  [ ] 官网/notice 原文钟点是哪个时区?
  [ ] 已用 make_et_iso / 美东本地钟点写入?
  [ ] 校验打印的「ET | 北京」两列都合理?
  [ ] 国会听证不应出现 ET 晚上 18:00+ (那是北京次日上午的典型错误形态)
  [ ] 已用 --ref-table 确认所有事件 DOW 正确
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 允许直接 `python scripts/validate_calendar_dates.py` (无安装包时)
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gold_miner.data.calendar_time_rules import (  # noqa: E402
    check_event_clock,
    check_event_dow,
    dual_clock_str,
    generate_dow_reference_table,
    to_beijing,
)

CALENDAR_FILE = _ROOT / "data" / "calendar_events.jsonl"


def validate() -> tuple[list[str], list[str]]:
    """校验所有日历事件, 返回 (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    if not CALENDAR_FILE.exists():
        return errors, warnings

    with open(CALENDAR_FILE, encoding="utf-8") as f:
        events: list[dict[str, Any]] = [json.loads(line) for line in f if line.strip()]

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

        dual = dual_clock_str(dt) if dt.tzinfo else f"naive {scheduled}"

        # 无时区偏移 → 旧格式
        if dt.tzinfo is None:
            warnings.append(
                f"旧格式(无时区): [{etype}] {name} → {scheduled} (应加 -04:00/-05:00)"
            )

        # ---- 1. DOW (星期) 校验 ----
        for finding in check_event_dow(
            name=name, event_type=etype, scheduled_at=dt
        ):
            if finding.severity == "error":
                errors.append(finding.message)
            else:
                warnings.append(finding.message)

        # ---- 2. 钟点 / 双重换算硬规则 ----
        for finding in check_event_clock(
            name=name, event_type=etype, scheduled_at=dt
        ):
            if finding.severity == "error":
                errors.append(finding.message)
            else:
                warnings.append(finding.message)

    return errors, warnings


def print_ref_table(days_ahead: int = 30) -> None:
    """打印未来事件的 DOW 参考表 (Markdown 格式)."""
    if not CALENDAR_FILE.exists():
        print("⚠️ 日历文件不存在")
        return

    with open(CALENDAR_FILE, encoding="utf-8") as f:
        events: list[dict[str, Any]] = [json.loads(line) for line in f if line.strip()]

    print(generate_dow_reference_table(events, days_ahead=days_ahead))


def _print_status(errors: list[str], warnings: list[str]) -> None:
    if errors:
        print("🔴 日期/钟点/DOW 校验失败 — 以下事件必须修复 (禁止带错时继续分析):")
        for e in errors:
            print(f"  ❌ {e}")
    if warnings:
        print("🟡 日期/钟点/DOW 校验警告 — 请人工确认:")
        for w in warnings:
            print(f"  ⚠️  {w}")
    if not errors and not warnings:
        print("✅ 日历日期+钟点+DOW 校验全部通过")


def validate_completeness() -> tuple[list[str], list[str]]:
    """检查当月关键事件类别是否完整覆盖（美国+欧洲+英国+全球）."""
    try:
        from gold_miner.data.calendar import EventCalendar  # noqa: E402

        cal = EventCalendar()
        return cal.validate_calendar_completeness()
    except ImportError:
        return [], []


def _print_completeness(missing: list[str], warnings: list[str]) -> None:
    if missing:
        print("🟡 日历事件覆盖度检查 — 以下类别缺失 (分析前需手动搜索补充):")
        for m in missing:
            print(f"  {m}")
    if warnings:
        for w in warnings:
            print(f"  {w}")
    if not missing and not any("🔴" in m for m in missing):
        print("✅ 日历事件覆盖度检查通过")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="日历事件日期+钟点+DOW 三重校验"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="严格模式: 警告也导致 exit(1), 阻断分析 pipeline"
    )
    parser.add_argument(
        "--ref-table", type=int, default=0, metavar="DAYS",
        help="输出未来 DAYS 天的 DOW 参考表 (Markdown) 并退出"
    )
    args = parser.parse_args()

    if args.ref_table > 0:
        print_ref_table(days_ahead=args.ref_table)
        sys.exit(0)

    errors, warnings = validate()

    # 总是先输出 DOW 参考表头 (帮助 AI/人类校验输出)
    print("━━━ 日历 DOW 参考表 (未来 30 天) ━━━")
    print_ref_table(days_ahead=30)
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    _print_status(errors, warnings)

    # ---- 完整性检查 (新增) ----
    missing, completeness_warnings = validate_completeness()
    print()
    _print_completeness(missing, completeness_warnings)

    if errors:
        sys.exit(1)
    if args.strict and warnings:
        print("\n⚠️  --strict 模式: 存在警告, 退出码非零")
        sys.exit(2)
    sys.exit(0)
