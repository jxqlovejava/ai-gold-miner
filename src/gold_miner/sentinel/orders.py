"""条件单检查 — 从 JSONL 读取活跃订单, 判断是否接近触发."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ConditionalOrder


def load_active_orders(orders_path: Path) -> list[ConditionalOrder]:
    """加载活跃条件单."""
    if not orders_path.exists():
        return []
    orders: list[ConditionalOrder] = []
    try:
        for line in orders_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("status") != "active":
                continue
            orders.append(ConditionalOrder(
                id=d.get("id", ""),
                status=d.get("status", "active"),
                type=d.get("type", ""),
                direction=d.get("direction", "买入"),
                trigger_price=d.get("trigger_price", 0),
                quantity_g=d.get("quantity_g", 0),
                oco=d.get("oco"),
                note=d.get("note", ""),
            ))
    except (json.JSONDecodeError, OSError):
        return []
    return orders


def check_order_proximity(
    orders: list[ConditionalOrder],
    current_price: float,
    near_pct: float = 1.5,
) -> list[tuple[ConditionalOrder, float]]:
    """返回接近触发的订单及距离%.

    返回: [(order, distance_pct), ...], 仅包含距触发 ≤ near_pct 的订单.
    """
    nearby: list[tuple[ConditionalOrder, float]] = []
    for o in orders:
        if o.trigger_price <= 0:
            continue
        dist = abs(current_price - o.trigger_price) / o.trigger_price * 100
        if dist <= near_pct:
            nearby.append((o, dist))

        # OCO 订单额外检查止盈/止损价
        if o.type == "oco" and o.oco:
            for key, _label in [("take_profit", "止盈"), ("stop_loss", "止损")]:
                leg = o.oco.get(key)
                if leg and isinstance(leg, dict):
                    tp = leg.get("price", 0)
                    if tp > 0:
                        dist_tp = abs(current_price - tp) / tp * 100
                        if dist_tp <= near_pct:
                            nearby.append((o, dist_tp))

    nearby.sort(key=lambda x: x[1])  # 最近的在前
    return nearby
