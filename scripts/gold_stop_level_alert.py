#!/usr/bin/env python3
"""黄金四级止损提醒 — Hermes cron --no-agent 模式.

stdout 直接投递微信。空 stdout = 静默。
工作日 20:00 运行: 检查积存金现价是否触及/逼近四级止损位,
触及 → 输出提醒卡片; 未触及 → 静默 (避免每日噪音叠加到 18:00 预览)。

四级止损位 (优先从 portfolio.yaml 读取, 缺失时用 V8.1 计划值兜底):
  net_breakeven  avg_cost/(1-费率)  r025 ATR净保本地板 → 减核心仓一半
  secondary      成本-5%            机动+新加仓清出
  warn           成本-10%           核心仓全部清出
  hard           成本-30%           无条件清仓

设计要点 (2026-08-06):
  - 不在 894 挂自动卖出单 (与 905/880 买点冲突, 止损即接回摩擦)
  - 止损位只作「手动执行提醒」, 到点提醒用户手动决策
  - 触发条件: 现价 ≤ 任一挡位 (已触及) 或 ≤ 净保本×1.02 (逼近)
  - 仓位轻时(5.5%)可接受手动; 若 P1 成交后仓位变重, 需升级为挂单兜底

Hermes 约定:
  - stdout 打印提醒卡片 + hermes 投递微信
  - 空 stdout, exit 0 = 静默
  - 错误: stderr 打印, exit 1
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
PORTFOLIO = Path(os.environ.get("GOLD_PORTFOLIO", "/home/ubuntu/.hermes/gold/portfolio.yaml"))
ORDERS = Path(os.environ.get("GOLD_ORDERS", "/home/ubuntu/.hermes/gold/conditional_orders.jsonl"))

# V8.1 计划兜底值 (portfolio.yaml 缺失时使用; 2026-08-06)
_FALLBACK = {"avg_cost": 890.80, "sell_fee_pct": 0.4, "secondary_stop": 846, "warn_line": 802, "hard_stop": 624}

# 距最高挡 (净保本) 多少 % 内视为「逼近」
_APPROACH_PCT = 2.0

# 挡位定义: (portfolio key, 显示名, 动作)
_SPECS = [
    ("net_breakeven", "ATR净保本", "r025: 减核心仓一半(~6g)"),
    ("secondary", "成本-5%", "机动+新加仓清出"),
    ("warn", "成本-10%", "核心仓全部清出"),
    ("hard", "硬止损-30%", "无条件清仓"),
]


def _now() -> datetime:
    return datetime.now(BEIJING)


def _fetch_price_jd() -> dict | None:
    """获取积存金当前价 (京东民生)."""
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
            "change_pct": round((price - yesterday) / yesterday * 100, 2)
            if yesterday > 0
            else 0.0,
        }
    except Exception:
        return None


def _load_portfolio() -> dict | None:
    if not PORTFOLIO.exists():
        return None
    try:
        import yaml

        with open(PORTFOLIO) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _portfolio_snapshot(p: dict | None) -> str | None:
    if not p:
        return None
    try:
        pos = p["positions"]["gold_jd"]
        grams = pos["grams"]
        avg_cost = pos["avg_cost"]
        sell_fee_pct = pos.get("sell_fee_pct", 0.4)
        net_breakeven = avg_cost / (1 - sell_fee_pct / 100)
        return f"{grams}g @ ¥{avg_cost:.2f} | 净保本 ¥{net_breakeven:.2f}"
    except Exception:
        return None


def _active_orders() -> str:
    if not ORDERS.exists():
        return "未读取到"
    try:
        parts = []
        for ln in open(ORDERS):
            o = json.loads(ln)
            if o.get("status") == "active":
                t = o.get("type")
                if t == "oco":
                    tp = o["oco"]["take_profit"]
                    sl = o["oco"]["stop_loss"]
                    parts.append(f"OCO 止盈¥{tp['price']}/止损¥{sl['price']} ×{tp['quantity_g']}g")
                else:
                    parts.append(
                        f"{'买入' if o.get('direction') == '买入' else '卖出'}"
                        f"¥{o.get('trigger_price')} ×{o.get('quantity_g')}g"
                    )
        return " / ".join(parts) if parts else "无活跃单"
    except Exception:
        return "解析失败"


def _resolve_levels(portfolio: dict | None) -> dict[str, float]:
    """四级止损位: 优先 portfolio.yaml, 否则 V8.1 兜底."""
    if portfolio:
        try:
            pos = portfolio["positions"]["gold_jd"]
            avg_cost = float(pos["avg_cost"])
            sell_fee = float(pos.get("sell_fee_pct", 0.4))
            net_breakeven = round(avg_cost / (1 - sell_fee / 100), 2)
            secondary = float(pos.get("secondary_stop", 0)) or round(avg_cost * 0.95, 2)
            warn = float(pos.get("warn_line", 0)) or round(avg_cost * 0.90, 2)
            hard = float(pos.get("hard_stop", 0)) or round(avg_cost * 0.70, 2)
            return {"net_breakeven": net_breakeven, "secondary": secondary, "warn": warn, "hard": hard}
        except Exception:
            pass
    f = _FALLBACK
    return {
        "net_breakeven": round(f["avg_cost"] / (1 - f["sell_fee_pct"] / 100), 2),
        "secondary": f["secondary_stop"],
        "warn": f["warn_line"],
        "hard": f["hard_stop"],
    }


def _check_levels(price: float, levels: dict[str, float]) -> list[dict]:
    """返回触及/逼近的止损挡, 按挡位由高到低 (距现价由近到远)."""
    touched = []
    for key, label, action in _SPECS:
        level = levels[key]
        dist_pct = (price - level) / level * 100
        if price <= level:
            status = "已触及"
        elif dist_pct <= _APPROACH_PCT:
            status = "逼近"
        else:
            continue
        touched.append(
            {"label": label, "level": level, "dist_pct": dist_pct, "status": status, "action": action}
        )
    touched.sort(key=lambda x: x["level"], reverse=True)
    return touched


def _card(now: datetime, price: dict | None, portfolio: str | None, orders: str, touched: list[dict]) -> str:
    lines = [
        f"🚨 四级止损提醒 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    if portfolio:
        lines.append(f"📦 持仓: {portfolio}")
    lines.append(f"🔖 条件单: {orders}")
    lines.append("")

    lines.append("⚠️ 触及/逼近止损位 (高→低):")
    for t in touched:
        flag = "已触及" if t["status"] == "已触及" else "逼近"
        lines.append(f"• ¥{t['level']:.2f} {t['label']}  [{flag}] 距 {t['dist_pct']:+.1f}%")
        lines.append(f"   → {t['action']}")

    lines.append("")
    lines.append("💡 纪律: 894/846/802 需手动执行; 624 无条件清仓;")
    lines.append("   与 905/880 买点冲突时以低吸主线优先, 止损只设在低吸失败位")
    lines.append("")
    lines.append(f"🤖 四级止损提醒 · {now.strftime('%H:%M')}")
    return "\n".join(lines)


def main() -> int:
    now = _now()
    if now.weekday() >= 5:
        return 0  # 周六日积存金休市, 不提醒

    price = _fetch_price_jd()
    if price is None:
        return 0  # 拉不到价 → 静默, 不误报

    portfolio = _load_portfolio()
    levels = _resolve_levels(portfolio)
    touched = _check_levels(price["price"], levels)
    if not touched:
        return 0  # 未触及/未逼近 → 静默

    print(_card(now, price, _portfolio_snapshot(portfolio), _active_orders(), touched), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 四级止损提醒异常: {e}", file=sys.stderr)
        sys.exit(1)
