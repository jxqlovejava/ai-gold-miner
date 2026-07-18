# -*- coding: utf-8 -*-
"""黄金哨兵 — 数据模型."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class AlertLevel(str, Enum):
    P0 = "p0"  # 止损触发 / 硬止损
    P1 = "p1"  # 接近止损 / 日内大跌
    P2 = "p2"  # 条件单接近 / 日历提醒


@dataclass
class GoldQuote:
    """黄金报价."""
    symbol: str  # "XAUUSD" or "积存金"
    price: float
    currency: str  # "USD" or "CNY"
    change_pct: float  # 日内涨跌幅 %
    prev_close: float
    source: str
    fetched_at: datetime


@dataclass
class ConditionalOrder:
    """条件单."""
    id: str
    status: str  # active / triggered / cancelled
    type: str  # limit_buy / oco
    direction: str  # 买入 / 卖出
    trigger_price: float
    quantity_g: float
    oco: Optional[dict] = None  # {take_profit, stop_loss}
    note: str = ""


@dataclass
class PortfolioSnapshot:
    """持仓快照."""
    instrument: str
    platform: str
    grams: float
    avg_cost: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    hard_stop: float
    secondary_stop: float


@dataclass
class SentinelConfig:
    """哨兵配置."""
    # 路径
    portfolio_path: Path = Path("data/private/portfolio.yaml")
    orders_path: Path = Path("data/private/conditional_orders.jsonl")
    calendar_path: Path = Path("data/calendar_events.jsonl")
    state_path: Path = Path("data/sentinel_state.json")

    # 阈值
    stop_near_pct: float = 2.0        # 距止损 ≤ 2% 预警
    day_drop_pct: float = 2.0         # 日内跌 > 2% 告警
    day_rise_pct: float = 3.0         # 日内涨 > 3% 告警
    order_near_pct: float = 1.5       # 距条件单 ≤ 1.5% 提醒

    # 日历
    event_remind_hours: int = 24      # 事件前 N 小时提醒

    # 冷却 (分钟)
    cool_p0: int = 5
    cool_p1: int = 30
    cool_p2: int = 120

    force: bool = False


@dataclass
class SentinelAlert:
    """哨兵告警."""
    level: AlertLevel
    title: str
    detail: str
    suggestion: str = ""
    triggered_at: datetime = field(default_factory=datetime.now)


@dataclass
class SentinelResult:
    """哨兵运行结果."""
    alerts: list[SentinelAlert] = field(default_factory=list)
    quotes: list[GoldQuote] = field(default_factory=list)
    portfolio: Optional[PortfolioSnapshot] = None

    @property
    def silent(self) -> bool:
        return len(self.alerts) == 0

    @property
    def message(self) -> str:
        if not self.alerts:
            return ""
        return format_alerts(self.alerts, self.quotes, self.portfolio)


def format_alerts(
    alerts: list[SentinelAlert],
    quotes: list[GoldQuote],
    portfolio: Optional[PortfolioSnapshot] = None,
) -> str:
    """格式化告警为人话卡片."""
    from datetime import datetime

    BEIJING = __import__('datetime').timezone(__import__('datetime').timedelta(hours=8))
    now = datetime.now(BEIJING).strftime("%m-%d %H:%M")

    lines = [f"🪙 黄金哨兵 · {now}"]

    # 行情快照
    for q in quotes:
        emoji = "🔴" if q.change_pct < 0 else "🟢"
        lines.append(
            f"{emoji} {q.symbol}: {q.price:.2f} {q.currency} "
            f"({q.change_pct:+.2f}%)"
        )

    if portfolio:
        p = portfolio
        pnl_emoji = "🔴" if p.unrealized_pnl < 0 else "🟢"
        lines.append(
            f"📊 持仓: {p.grams:.2f}g @ {p.avg_cost:.0f}元 "
            f"| 市值 ¥{p.market_value:.0f} "
            f"| {pnl_emoji} {p.unrealized_pnl:+.0f}元 ({p.unrealized_pnl_pct:+.1f}%)"
        )
        # 止损距离
        dist_to_stop = (p.current_price - p.secondary_stop) / p.secondary_stop * 100
        lines.append(f"🛑 止损距: {dist_to_stop:+.1f}% (止损{p.secondary_stop}元)")

    # 分级告警
    p0_alerts = [a for a in alerts if a.level == AlertLevel.P0]
    p1_alerts = [a for a in alerts if a.level == AlertLevel.P1]
    p2_alerts = [a for a in alerts if a.level == AlertLevel.P2]

    if p0_alerts:
        lines.append("")
        lines.append("🚨 P0 紧急:")
        for a in p0_alerts:
            lines.append(f"  ❌ {a.title}")
            lines.append(f"     {a.detail}")
            if a.suggestion:
                lines.append(f"     💡 {a.suggestion}")

    if p1_alerts:
        lines.append("")
        lines.append("⚠️ P1 关注:")
        for a in p1_alerts:
            lines.append(f"  • {a.title}")
            lines.append(f"    {a.detail}")

    if p2_alerts:
        lines.append("")
        lines.append("ℹ️ P2 提醒:")
        for a in p2_alerts:
            lines.append(f"  • {a.title}")

    return "\n".join(lines)
