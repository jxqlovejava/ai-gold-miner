#!/usr/bin/env python3
"""晚间事件预告 — 北京时间 18:00 运行 → 推送今晚美国时段重要事件到微信.

Hermes 约定:
  - 有今晚事件: stdout 打印卡片 + hermes send 推送微信
  - 无今晚事件: stdout 为空, exit 0 (静默)
  - 错误: stderr 打印, exit 1

覆盖:
  1. 今晚(T-1 18:00 ~ T+1 06:00 BJT)高影响事件
  2. 即将发生的 Monitor 事件
  3. 当前持仓+金价快照

用法:
  PYTHONPATH=src python3 scripts/evening_event_preview.py

cron (北京时间 18:00, Mon-Fri):
  0 18 * * 1-5 cd /path/to/ai-gold-miner && PYTHONPATH=src python3 scripts/evening_event_preview.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _now() -> datetime:
    return datetime.now(BEIJING)


def _send_hermes(message: str) -> bool:
    """通过 Hermes 推送微信通知."""
    try:
        result = subprocess.run(
            ["hermes", "send", "--to", "weixin", message],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _fetch_price_jd() -> dict | None:
    """获取积存金当前价."""
    try:
        import httpx
        resp = httpx.get(
            "https://ms.jr.jd.com/gw/generic/hj/h5/m/latestPrice",
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                "Referer": "https://m.jd.com/",
            },
            timeout=8.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success"):
            return None
        result_data = data.get("resultData", {})
        datas = result_data.get("datas", {}) if isinstance(result_data, dict) else {}
        price = float(datas.get("price", 0))
        yesterday = float(datas.get("yesterdayPrice", 0))
        if price <= 0:
            return None
        return {
            "price": round(price, 2),
            "change_pct": round((price - yesterday) / yesterday * 100, 2) if yesterday > 0 else 0.0,
        }
    except Exception:
        return None


def _get_tonight_events() -> list[dict]:
    """获取今晚 (未来 12 小时内) 的重要事件."""
    events: list[dict] = []
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))
        from gold_miner.data.calendar import EventCalendar, EventImpact

        cal = EventCalendar()
        now = _now()
        tonight_end = now + timedelta(hours=12)

        # 高影响事件
        upcoming = cal.get_upcoming(days=1, min_impact=EventImpact.MEDIUM)
        for e in upcoming:
            et = e.scheduled_at
            bj = et.astimezone(BEIJING)
            if now <= bj <= tonight_end:
                impact_icon = {"high": "🔴", "extreme": "💀", "medium": "🟡", "low": "⚪"}
                events.append({
                    "name": e.name,
                    "time_bj": bj.strftime("%H:%M"),
                    "time_et": et.strftime("%H:%M ET"),
                    "impact": e.impact.value,
                    "icon": impact_icon.get(e.impact.value, "⚪"),
                    "description": e.description[:100] if e.description else "",
                })

        # 活跃 Monitor
        monitors = cal.get_active_monitors()
        for m in monitors:
            events.append({
                "name": f"📡 {m.name}",
                "time_bj": "—",
                "time_et": "—",
                "impact": "monitor",
                "icon": "📡",
                "description": (m.trigger_condition or "")[:100],
            })

    except Exception:
        pass

    return events


def _get_portfolio_snapshot() -> str | None:
    """持仓快照."""
    portfolio_path = PROJECT_ROOT / "data/private/portfolio.yaml"
    if not portfolio_path.exists():
        return None
    try:
        import yaml
        with open(portfolio_path) as f:
            p = yaml.safe_load(f)
        pos = p["positions"]["gold_jd"]
        grams = pos["grams"]
        avg_cost = pos["avg_cost"]
        return f"{grams}g @ ¥{avg_cost:.0f}"
    except Exception:
        return None


def main() -> int:
    now = _now()

    # 周末静默
    if now.weekday() >= 5:
        return 0

    events = _get_tonight_events()
    if not events:
        return 0  # 无今晚事件, 静默

    price_info = _fetch_price_jd()
    portfolio = _get_portfolio_snapshot()

    # ── 格式化 ──
    lines = [
        f"🌙 今晚事件预告 | {now.strftime('%m/%d %H:%M')}",
        f"━━━━━━━━━━━━━━━━━━━",
    ]

    if price_info:
        lines.append(f"💰 积存金: ¥{price_info['price']:.2f} ({price_info['change_pct']:+.2f}%)")
    if portfolio:
        lines.append(f"📦 持仓: {portfolio}")
    lines.append("")

    # 分类事件
    data_events = [e for e in events if e["impact"] != "monitor"]
    monitor_events = [e for e in events if e["impact"] == "monitor"]

    if data_events:
        lines.append("📅 今晚数据/事件 (18:00~次日06:00 BJT):")
        for e in data_events:
            lines.append(
                f"  {e['icon']} {e['time_bj']} BJT ({e['time_et']}) | {e['name']}"
            )
            if e.get("description"):
                lines.append(f"     {e['description']}")
        lines.append("")

    if monitor_events:
        lines.append("📡 活跃监控条件:")
        for m in monitor_events[:5]:
            lines.append(f"  {m['name']}")
            if m.get("description"):
                lines.append(f"     {m['description']}")
        lines.append("")

    lines.extend([
        "💡 提醒:",
        "• 重大数据前检查条件单是否合理",
        "• r004: 数据前2小时不新建重仓(>10%)",
        "• 盈亏超过阈值及时调整OCO",
        "",
        f"🤖 自动推送 · {now.strftime('%H:%M')}",
    ])

    card = "\n".join(lines)
    print(card, flush=True)
    _send_hermes(card)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 晚间事件预告异常: {e}", file=sys.stderr)
        sys.exit(1)
