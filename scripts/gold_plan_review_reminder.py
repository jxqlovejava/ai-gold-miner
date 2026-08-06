#!/usr/bin/env python3
"""V8.1 双窗落地后重估提醒 — Hermes cron --no-agent 模式.

stdout 直接投递到微信。空 stdout = 静默。
8/7 晚间运行 (20:37 附近): 非农 20:30 已发布 + 美伊协议"48h倒计时"大概率已见分晓。

Hermes 约定:
  - stdout 打印提醒卡片 + hermes 投递微信
  - 空 stdout, exit 0 = 静默
  - 错误: stderr 打印, exit 1
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
ROOT = Path("/home/ubuntu/ai-gold-miner")
PORTFOLIO = Path("/home/ubuntu/.hermes/gold/portfolio.yaml")
ORDERS = Path("/home/ubuntu/.hermes/gold/conditional_orders.jsonl")


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


def _get_portfolio() -> str | None:
    """持仓快照 (服务器真相源 portfolio.yaml)."""
    if not PORTFOLIO.exists():
        return None
    try:
        import yaml

        with open(PORTFOLIO) as f:
            p = yaml.safe_load(f)
        pos = p["positions"]["gold_jd"]
        grams = pos["grams"]
        avg_cost = pos["avg_cost"]
        sell_fee_pct = pos.get("sell_fee_pct", 0.4)
        net_breakeven = avg_cost / (1 - sell_fee_pct / 100)
        return f"{grams}g @ ¥{avg_cost:.2f} | 净保本 ¥{net_breakeven:.2f}"
    except Exception:
        return None


def _get_active_orders() -> str:
    """服务器活跃条件单快照."""
    if not ORDERS.exists():
        return "未读取到"
    try:
        import json

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


def _card(now: datetime, price: dict | None, portfolio: str | None, orders: str) -> str:
    """双窗落地后重估卡片."""
    lines = [
        f"🎯 V8.1计划 · 双窗落地复查 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    if portfolio:
        lines.append(f"📦 持仓: {portfolio}")
    lines.append(f"🔖 条件单: {orders}")
    lines.append("")

    lines.extend([
        "📡 今晚两大结果已落地, 请核对:",
        "① 霍尔木兹协议 (特朗普'48h倒计时' 8/6-8/8):",
        "   → 已签 = 溢价回吐+油价反弹, 905/880低吸接货",
        "   → 未签 = 避险延续, 持有为主",
        "② 非农 20:30 已发布:",
        "   → <100K 利多金价 / >150K 利空",
        "",
        "🔄 建议核对动作 (平台限2笔限价买单):",
        "• 若 880×15g 已成交 → 撤 905×5g, 重挂 860 深水档",
        "• 若两档均未成交 → 评估是否市价补仓 (视协议+非农结果)",
        "• 若协议签署+金价不深跌 → 905 档已接住, 880 留观察",
        "",
        "💡 纪律: 让条件单回答, 不追高; 若急涨950+无卖出单, 需手动评估减仓",
        "",
        f"🤖 V8.1计划提醒 · {now.strftime('%H:%M')}",
    ])
    return "\n".join(lines)


def main() -> int:
    now = _now()
    # 仅 8/7 (周五) 晚间运行
    if now.month != 8 or now.day != 7:
        return 0
    if now.weekday() >= 6:
        return 0
    if not (20 <= now.hour <= 23):
        return 0

    price = _fetch_price_jd()
    portfolio = _get_portfolio()
    orders = _get_active_orders()
    print(_card(now, price, portfolio, orders), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ V8.1重估提醒异常: {e}", file=sys.stderr)
        sys.exit(1)
