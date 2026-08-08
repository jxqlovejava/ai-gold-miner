"""黄金哨兵 — 数据模型."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class AlertLevel(StrEnum):
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
    oco: dict | None = None  # {take_profit, stop_loss}
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
    atr_stop_price: float = 0.0   # r025 ATR 移动止盈位 (盘中自动计算)


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
    portfolio: PortfolioSnapshot | None = None

    @property
    def silent(self) -> bool:
        return len(self.alerts) == 0

    @property
    def message(self) -> str:
        if not self.alerts:
            return ""
        return format_alerts(self.alerts, self.quotes, self.portfolio)


_BANK_CN = {"MS": "民生银行", "JD": "京东金融"}


def symbol_cn(symbol: str) -> str:
    """报价代码 → 中文名称（人话）.

    XAUUSD → 国际金价（XAUUSD）; 积存金(MS) → 积存金（民生银行）
    """
    if symbol == "XAUUSD":
        return "国际金价（XAUUSD）"
    m = re.fullmatch(r"积存金\((\w+)\)", symbol)
    if m:
        bank = _BANK_CN.get(m.group(1), m.group(1))
        return f"积存金（{bank}）"
    return symbol


def currency_cn(currency: str) -> str:
    """货币代码 → 中文单位."""
    return {"USD": "美元", "CNY": "元/克"}.get(currency, currency)


def _quote_line(q: GoldQuote) -> str:
    """单条行情 → 人话."""
    if q.change_pct == 0:
        chg = "持平"
    elif q.change_pct > 0:
        chg = f"上涨 {q.change_pct:.2f}%"
    else:
        chg = f"下跌 {abs(q.change_pct):.2f}%"
    return (
        f"{symbol_cn(q.symbol)} {q.price:.2f} {currency_cn(q.currency)}，"
        f"较昨收 {q.prev_close:.2f} {chg}"
    )


def format_alerts(
    alerts: list[SentinelAlert],
    quotes: list[GoldQuote],
    portfolio: PortfolioSnapshot | None = None,
) -> str:
    """格式化告警为人话卡片（Hermes 微信推送）."""
    from datetime import datetime

    beijing = __import__('datetime').timezone(__import__('datetime').timedelta(hours=8))
    now = datetime.now(beijing).strftime("%m-%d %H:%M")

    lines = [f"🪙 黄金哨兵 · 青蚨 · {now}"]

    # ── 行情 ──
    if quotes:
        lines.append("")
        lines.append("📈 行情")
        for q in quotes:
            lines.append(f"  {_quote_line(q)}")

    # ── 持仓 ──
    if portfolio:
        p = portfolio
        lines.append("")
        lines.append("📊 你的持仓")
        pnl_text = (
            f"浮盈 {p.unrealized_pnl:+.0f} 元（{p.unrealized_pnl_pct:+.1f}%）"
            if p.unrealized_pnl >= 0
            else f"浮亏 {abs(p.unrealized_pnl):.0f} 元（{p.unrealized_pnl_pct:.1f}%）"
        )
        lines.append(
            f"  你持有 {p.grams:.2f} 克，成本均价 {p.avg_cost:.0f} 元/克，"
            f"当前市值 ¥{p.market_value:.0f}，{pnl_text}"
        )
        if p.secondary_stop > 0:
            dist = (p.current_price - p.secondary_stop) / p.secondary_stop * 100
            if dist > 5:
                status = "，安全"
            elif dist > 2:
                status = "，已接近止损位"
            else:
                status = "，⚠️ 危险"
            lines.append(f"  止损线 {p.secondary_stop:.0f} 元，现价距止损还有 {dist:.1f}%{status}")
        if p.atr_stop_price > 0:
            # r025 ATR 移动止盈: 现价在止盈位上方=持有, 跌破=触发减仓
            atr_dist = (p.current_price - p.atr_stop_price) / p.atr_stop_price * 100
            lines.append(
                f"  🎯 ATR止盈 {p.atr_stop_price:.2f} 元，距现价 {atr_dist:+.1f}%"
                + ("，跌破即减半" if atr_dist < 3 else "")
            )

    # ── 分级提醒（人话标题，不带 P 代码）──
    sections = [
        (AlertLevel.P0, "🚨 紧急处理", "•"),
        (AlertLevel.P1, "⚠️ 需要关注", "•"),
        (AlertLevel.P2, "💡 例行提醒", "•"),
    ]
    for level, header, bullet in sections:
        group = [a for a in alerts if a.level == level]
        if not group:
            continue
        lines.append("")
        lines.append(header)
        for a in group:
            lines.append(f"{bullet} {a.title}")
            if a.detail:
                lines.append(f"    {a.detail}")
            if a.suggestion:
                lines.append(f"    💡 {a.suggestion}")

    return "\n".join(lines)
