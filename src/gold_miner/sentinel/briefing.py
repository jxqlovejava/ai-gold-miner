"""每日简报 / 每周总结 — Hermes 定时推送."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .engine import SentinelConfig, SentinelEngine
from .orders import load_active_orders

BEIJING = timezone(timedelta(hours=8))


def generate_daily_briefing(config: SentinelConfig) -> str:
    """生成每日盘前简报 (人话 Markdown 卡片).

    包含: 隔夜行情 / 持仓快照 / 今日事件 / 条件单状态 / 关键位
    """
    engine = SentinelEngine(config)
    result = engine.run()

    now = datetime.now(BEIJING)
    today_str = now.strftime("%Y-%m-%d")
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]

    lines = [
        f"🪙 黄金盘前简报 · {today_str} {weekday}",
        "",
    ]

    # ── 1. 行情快照 ──
    if result.quotes:
        lines.append("━━━ 行情快照 ━━━")
        for q in result.quotes:
            emoji = "🔴" if q.change_pct < 0 else "🟢"
            lines.append(
                f"{emoji} {q.symbol}: {q.price:.2f} {q.currency} "
                f"({q.change_pct:+.2f}%) | 来源: {q.source}"
            )

    # ── 2. 持仓状态 ──
    if result.portfolio:
        p = result.portfolio
        lines.append("")
        lines.append("━━━ 持仓状态 ━━━")
        pnl_emoji = "🔴" if p.unrealized_pnl < 0 else "🟢"
        lines.append(f"📊 {p.instrument} ({p.platform})")
        lines.append(f"   持仓: {p.grams:.2f}g @ ¥{p.avg_cost:.0f}")
        lines.append(f"   市值: ¥{p.market_value:.0f}")
        lines.append(f"   浮盈: {pnl_emoji} ¥{p.unrealized_pnl:+.0f} ({p.unrealized_pnl_pct:+.1f}%)")

        # 止损距离
        if p.secondary_stop > 0:
            dist = (p.current_price - p.secondary_stop) / p.secondary_stop * 100
            status = "🟢 安全" if dist > 5 else ("🟡 接近" if dist > 2 else "🔴 危险")
            lines.append(f"   止损: ¥{p.secondary_stop} (距{status} {dist:+.1f}%)")

    # ── 3. 活跃条件单 ──
    orders = load_active_orders(config.orders_path)
    if orders:
        lines.append("")
        lines.append("━━━ 活跃条件单 ━━━")
        for o in orders:
            tp_str = ""
            if o.type == "oco" and o.oco:
                tp = o.oco.get("take_profit", {})
                sl = o.oco.get("stop_loss", {})
                if isinstance(tp, dict) and isinstance(sl, dict):
                    tp_str = f" 止盈¥{tp.get('price','?')}/止损¥{sl.get('price','?')}"
            lines.append(
                f"  • {o.type.upper()} {o.direction} "
                f"@¥{o.trigger_price:.0f} ×{o.quantity_g}g{tp_str}"
            )
    else:
        lines.append("")
        lines.append("━━━ 活跃条件单 ━━━")
        lines.append("  (无活跃条件单)")

    # ── 4. 今日事件 ──
    events = _get_today_events(config)
    lines.append("")
    lines.append("━━━ 今日事件 ━━━")
    if events:
        for e in events:
            impact_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(e.get("impact", ""), "⚪")
            name = e.get("name", "")
            sat_str = e.get("scheduled_at", "")
            bj_time = ""
            if sat_str:
                try:
                    sat = datetime.fromisoformat(sat_str)
                    bj_time = sat.astimezone(BEIJING).strftime("%H:%M")
                except ValueError:
                    pass
            lines.append(f"  {impact_icon} {name} ({bj_time} 北京)" if bj_time else f"  {impact_icon} {name}")
    else:
        lines.append("  ✅ 今日无重大事件")

    # ── 5. 本周展望 ──
    lines.append("")
    lines.append("━━━ 本周关注 ━━━")
    upcoming = _get_upcoming_events(config, days=7)
    if upcoming:
        for e in upcoming[:5]:
            name = e.get("name", "")
            sat_str = e.get("scheduled_at", "")
            if sat_str:
                try:
                    sat = datetime.fromisoformat(sat_str)
                    bj = sat.astimezone(BEIJING)
                    lines.append(f"  📅 {bj.strftime('%m-%d %H:%M')} {name}")
                except ValueError:
                    pass

    # ── 6. 快速参考 ──
    lines.append("")
    lines.append("━━━ 关键价位 ━━━")
    if result.portfolio:
        p = result.portfolio
        lines.append(f"  成本均价: ¥{p.avg_cost:.0f}")
        lines.append(f"  二级止损: ¥{p.secondary_stop:.0f}")
        lines.append(f"  硬止损:   ¥{p.hard_stop:.0f}")

    # 告警摘要
    if result.alerts:
        lines.append("")
        lines.append("━━━ ⚠️ 活跃告警 ━━━")
        for a in result.alerts[:5]:
            lines.append(f"  [{a.level.value.upper()}] {a.title}")

    lines.append("")
    lines.append("─" * 20)
    lines.append("💡 黄金哨兵自动推送 · 下次更新: 明日盘前")

    return "\n".join(lines)


def generate_weekly_summary(config: SentinelConfig) -> str:
    """生成每周总结."""
    now = datetime.now(BEIJING)
    week_start = (now - timedelta(days=now.weekday())).strftime("%m-%d")
    week_end = now.strftime("%m-%d")

    engine = SentinelEngine(config)
    result = engine.run()

    lines = [
        f"🪙 黄金周报 · {week_start} ~ {week_end}",
        "",
    ]

    # 行情
    if result.quotes:
        for q in result.quotes:
            lines.append(f"{q.symbol}: {q.price:.2f} {q.currency} (周变 {q.change_pct:+.2f}%)")

    # 持仓
    if result.portfolio:
        p = result.portfolio
        lines.append(f"📊 持仓浮盈: ¥{p.unrealized_pnl:+.0f} ({p.unrealized_pnl_pct:+.1f}%)")

    # 下周事件
    lines.append("")
    lines.append("━━━ 下周关键事件 ━━━")
    upcoming = _get_upcoming_events(config, days=7)
    if upcoming:
        for e in upcoming[:8]:
            name = e.get("name", "")
            sat_str = e.get("scheduled_at", "")
            if sat_str:
                try:
                    sat = datetime.fromisoformat(sat_str)
                    bj = sat.astimezone(BEIJING)
                    day_cn = ["周一","周二","周三","周四","周五","周六","周日"][bj.weekday()]
                    lines.append(f"  📅 {bj.strftime('%m-%d %H:%M')} {day_cn} {name}")
                except ValueError:
                    pass
    else:
        lines.append("  ✅ 下周暂无重大事件")

    # 军规提醒
    lines.append("")
    lines.append("━━━ 风控检查 ━━━")
    if result.portfolio:
        p = result.portfolio
        checks = []
        if p.unrealized_pnl_pct <= -5:
            checks.append("⚠️ r022: 浮亏超5%, 注意决策质量")
        if p.unrealized_pnl_pct >= 15:
            checks.append("💡 r010: 浮盈>15%, 考虑上移止损")
        if p.current_price > p.secondary_stop * 1.15:
            checks.append("✅ 距二级止损>15%, 安全区间")
        for c in checks:
            lines.append(f"  {c}")
        if not checks:
            lines.append("  ✅ 风控指标正常")

    lines.append("")
    lines.append("💡 下周继续执行条件单策略, 不追涨杀跌")

    return "\n".join(lines)


def _get_today_events(config: SentinelConfig) -> list[dict]:
    """获取今日日历事件."""
    if not config.calendar_path.exists():
        return []
    now = datetime.now(BEIJING)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)

    events = []
    try:
        for line in config.calendar_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            d = json.loads(line)
            sat_str = d.get("scheduled_at", "")
            if not sat_str:
                continue
            try:
                sat = datetime.fromisoformat(sat_str)
            except ValueError:
                continue
            bj = sat.astimezone(BEIJING) if sat.tzinfo else sat
            if today_start <= bj < today_end:
                events.append(d)
    except Exception:
        pass

    events.sort(key=lambda e: e.get("scheduled_at", ""))
    return events


def _get_upcoming_events(config: SentinelConfig, days: int = 7) -> list[dict]:
    """获取未来 N 天日历事件."""
    if not config.calendar_path.exists():
        return []
    now = datetime.now(BEIJING)
    cutoff = now + timedelta(days=days)

    events = []
    try:
        for line in config.calendar_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            d = json.loads(line)
            sat_str = d.get("scheduled_at", "")
            if not sat_str:
                continue
            try:
                sat = datetime.fromisoformat(sat_str)
            except ValueError:
                continue
            bj = sat.astimezone(BEIJING) if sat.tzinfo else sat
            if now <= bj <= cutoff and not d.get("actual"):
                events.append(d)
    except Exception:
        pass

    events.sort(key=lambda e: e.get("scheduled_at", ""))
    return events
