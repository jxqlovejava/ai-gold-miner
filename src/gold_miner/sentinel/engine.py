# -*- coding: utf-8 -*-
"""黄金哨兵引擎 — 持仓监控 + 价格告警 + 条件单检查."""

from __future__ import annotations

import json
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import (
    AlertLevel,
    ConditionalOrder,
    GoldQuote,
    PortfolioSnapshot,
    SentinelAlert,
    SentinelConfig,
    SentinelResult,
)
from .orders import check_order_proximity, load_active_orders
from .quotes import fetch_quotes

BEIJING = timezone(timedelta(hours=8))


class SentinelEngine:
    """黄金哨兵引擎."""

    def __init__(self, config: SentinelConfig):
        self.cfg = config

    def run(self) -> SentinelResult:
        """执行一次哨兵检查."""
        alerts: list[SentinelAlert] = []

        # 1. 获取报价
        quotes = fetch_quotes()
        if not quotes:
            return SentinelResult(alerts=alerts)

        xauusd = next((q for q in quotes if q.symbol == "XAUUSD"), None)
        jd_gold = next((q for q in quotes if "积存金" in q.symbol), None)
        current_price = jd_gold.price if jd_gold else (xauusd.price if xauusd else 0)

        # 2. 价格异动告警
        for q in quotes:
            if abs(q.change_pct) >= self.cfg.day_drop_pct:
                direction = "大跌" if q.change_pct < 0 else "大涨"
                level = AlertLevel.P1 if abs(q.change_pct) < self.cfg.day_rise_pct + 2 else AlertLevel.P0
                alerts.append(SentinelAlert(
                    level=level,
                    title=f"{q.symbol} 日内{direction} {q.change_pct:+.2f}%",
                    detail=f"当前 {q.price:.2f} {q.currency}, 前收 {q.prev_close:.2f}",
                ))

        # 3. 持仓分析
        portfolio = self._load_portfolio(current_price)
        if portfolio:
            alerts.extend(self._check_portfolio(portfolio))

        # 4. 条件单检查
        if current_price > 0:
            orders = load_active_orders(self.cfg.orders_path)
            alerts.extend(self._check_orders(orders, current_price))

        # 5. 日历事件提醒
        alerts.extend(self._check_calendar())

        return SentinelResult(
            alerts=alerts,
            quotes=quotes,
            portfolio=portfolio,
        )

    def _load_portfolio(self, current_price: float) -> Optional[PortfolioSnapshot]:
        """加载持仓并计算快照."""
        pf_path = self.cfg.portfolio_path
        if not pf_path.exists():
            return None
        try:
            data = yaml.safe_load(pf_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        positions = data.get("positions", {})
        gold = positions.get("gold_jd", {})
        limits = data.get("limits", {})

        grams = gold.get("grams", 0)
        avg_cost = gold.get("avg_cost", 0)
        hard_stop = gold.get("hard_stop", 0)
        secondary_stop = gold.get("secondary_stop", 0)

        if grams <= 0 or avg_cost <= 0:
            return None

        market_value = grams * current_price
        cost_value = grams * avg_cost
        unrealized_pnl = market_value - cost_value
        unrealized_pnl_pct = (unrealized_pnl / cost_value * 100) if cost_value > 0 else 0

        return PortfolioSnapshot(
            instrument=gold.get("instrument", "积存金"),
            platform=gold.get("platform", "京东金融"),
            grams=grams,
            avg_cost=avg_cost,
            current_price=current_price,
            market_value=market_value,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            hard_stop=hard_stop,
            secondary_stop=secondary_stop,
        )

    def _check_portfolio(self, p: PortfolioSnapshot) -> list[SentinelAlert]:
        """持仓风险检查."""
        alerts: list[SentinelAlert] = []

        # 硬止损触发
        if p.current_price <= p.hard_stop:
            alerts.append(SentinelAlert(
                level=AlertLevel.P0,
                title="🔴 硬止损触发!",
                detail=f"当前价{p.current_price:.0f}元 ≤ 硬止损{p.hard_stop}元",
                suggestion="立即按止损价清仓, 认赔离场",
            ))
            return alerts

        # 接近硬止损
        dist_to_hard = (p.current_price - p.hard_stop) / p.hard_stop * 100
        if dist_to_hard <= 5:
            alerts.append(SentinelAlert(
                level=AlertLevel.P1,
                title=f"接近硬止损 ({dist_to_hard:.1f}% 距离)",
                detail=f"当前{p.current_price:.0f}元, 硬止损{p.hard_stop}元",
            ))

        # 二级止损
        if p.secondary_stop > 0 and p.current_price <= p.secondary_stop:
            alerts.append(SentinelAlert(
                level=AlertLevel.P0,
                title="🔴 二级止损触发!",
                detail=f"当前价{p.current_price:.0f}元 ≤ OCO止损{p.secondary_stop}元",
                suggestion="检查条件单 co_20260716_003 是否已触发卖出9g",
            ))
        elif p.secondary_stop > 0:
            dist_to_sec = (p.current_price - p.secondary_stop) / p.secondary_stop * 100
            if dist_to_sec <= self.cfg.stop_near_pct:
                alerts.append(SentinelAlert(
                    level=AlertLevel.P1,
                    title=f"接近二级止损 ({dist_to_sec:.1f}% 距离)",
                    detail=f"当前{p.current_price:.0f}元, 止损{p.secondary_stop}元",
                ))

        # 大幅浮亏
        if p.unrealized_pnl_pct <= -10:
            alerts.append(SentinelAlert(
                level=AlertLevel.P1,
                title=f"浮亏达 {p.unrealized_pnl_pct:+.1f}%",
                detail=f"成本{p.avg_cost:.0f}元, 当前{p.current_price:.0f}元, 浮亏{p.unrealized_pnl:+.0f}元",
                suggestion="r022: 浮亏超10%后决策质量骤降, 提前动作不要等",
            ))

        # 浮盈上移止损提醒
        if p.unrealized_pnl_pct >= 20:
            alerts.append(SentinelAlert(
                level=AlertLevel.P2,
                title=f"浮盈 {p.unrealized_pnl_pct:+.1f}%, 检查止损上移",
                detail=f"r010: 浮盈>20%时止损必须上移至成本价以上",
            ))

        return alerts

    def _check_orders(
        self,
        orders: list[ConditionalOrder],
        current_price: float,
    ) -> list[SentinelAlert]:
        """条件单接近检查."""
        alerts: list[SentinelAlert] = []
        nearby = check_order_proximity(orders, current_price, self.cfg.order_near_pct)
        for o, dist in nearby[:3]:  # 最多3条
            direction_sym = "↓" if o.direction == "卖出" else "↑"
            alerts.append(SentinelAlert(
                level=AlertLevel.P2,
                title=f"条件单接近: {o.type} {o.direction} "
                      f"@{o.trigger_price}元 ({direction_sym}{dist:.1f}%)",
                detail=f"{o.note[:50] if o.note else o.id}",
            ))
        return alerts

    def _check_calendar(self) -> list[SentinelAlert]:
        """检查未来 N 小时内的日历事件."""
        alerts: list[SentinelAlert] = []
        cal_path = self.cfg.calendar_path
        if not cal_path.exists():
            return alerts

        now = datetime.now(timezone.utc)
        window = now + timedelta(hours=self.cfg.event_remind_hours)

        try:
            for line in cal_path.read_text(encoding="utf-8").strip().split("\n"):
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

                if sat.tzinfo is None:
                    continue

                if now <= sat <= window and d.get("impact") == "high":
                    name = d.get("name", "")
                    if d.get("actual"):
                        continue  # 已有结果的事件不提醒
                    alerts.append(SentinelAlert(
                        level=AlertLevel.P2,
                        title=f"📅 即将: {name}",
                        detail=f"时间: {sat.astimezone(BEIJING).strftime('%m-%d %H:%M')} (北京)",
                    ))
        except Exception:
            return []
        return alerts
