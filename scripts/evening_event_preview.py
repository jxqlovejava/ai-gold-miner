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
    """发送 macOS 桌面通知."""
    try:
        lines = message.strip().split("\n")
        title = lines[0][:100] if lines else "晚间事件预告"
        body = "\n".join(lines[1:5])[:200] if len(lines) > 1 else ""
        title_clean = title.replace('"', "'").replace("\\", "")
        body_clean = body.replace('"', "'").replace("\\", "")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{body_clean}" with title "{title_clean}" sound name "Glass"'],
            capture_output=True, timeout=10,
        )
        return True
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

        # 活跃 Monitor — 名称 + 精简触发条件
        monitors = cal.get_active_monitors()
        for m in monitors:
            events.append({
                "name": m.name.replace("观测: ", ""),
                "time_bj": "—",
                "time_et": "—",
                "impact": "monitor",
                "icon": "",
                "description": (m.trigger_condition or "").strip(),
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

    # ── 格式化: 信息完整但排版紧凑 ──
    lines = [f"🌙 今晚事件预告 · {now.strftime('%m/%d')}"]

    price_str = f"¥{price_info['price']:.2f} ({price_info['change_pct']:+.2f}%)" if price_info else ""
    port_str = f"持仓 {portfolio}" if portfolio else ""
    summary = " | ".join(x for x in [price_str, port_str] if x)
    if summary:
        lines.append(summary)

    # 分类事件
    data_events = [e for e in events if e["impact"] != "monitor"]
    monitor_events = [e for e in events if e["impact"] == "monitor"]

    if data_events:
        lines.append("")
        lines.append("📅 今晚数据")
        for e in data_events:
            name = e["name"]
            if "(" in name and name.endswith(")"):
                name = name.split(" (")[0]
            lines.append(f"· {e['time_bj']} | {name}")

    if monitor_events:
        lines.append("")
        lines.append("📡 关注信号")
        for m in monitor_events[:3]:
            desc = m.get("description", "")
            # 压缩触发条件: 单行, 截断到 ~34 字符
            cond = desc[:34] + ("…" if len(desc) > 34 else "") if desc else ""
            if cond:
                lines.append(f"· {m['name']}")
                lines.append(f"   → {cond}")
            else:
                lines.append(f"· {m['name']}")

    lines.extend([
        "",
        "💡 数据前不重仓(r004) · 条件单守门员",
        f"🤖 {now.strftime('%H:%M')}",
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
