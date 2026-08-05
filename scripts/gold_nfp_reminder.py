#!/usr/bin/env python3
"""V7 非农前复查提醒 — Hermes cron --no-agent 模式.

stdout 直接投递到微信。空 stdout = 静默。
8/7 两个时刻运行: 09:00 盘前 + 19:30 数据前, 每次输出对应阶段的复查卡片。

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


def _nfp_card(now: datetime, price: dict | None, portfolio: str | None) -> str:
    """非农前复查卡片."""
    lines = [
        f"🎯 V7计划 · 非农前复查 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    if portfolio:
        lines.append(f"📦 持仓: {portfolio}")
    lines.append("")

    hour = now.hour
    if hour < 12:
        lines.extend([
            "☀️ 【盘前 · 今日两大待决】",
            "① 霍尔木兹协议是否签署 (伊朗最高领袖审批最后一关)",
            "   → 签署 = 溢价回吐+油价反弹, 警惕双杀; 未签 = 二阶利多延续",
            "② 今晚20:30 非农 (预期80k, ADP +4.4万远低预期→或偏弱)",
            "   → 非农<100K 利多金价 / >150K 利空",
            "",
            "📋 V7 动作清单:",
            "• 不追高: 919已超布林上轨+RSI超买, 无追涨单",
            "• 条件单守门员: OCO 950/852×15g + 买860×10g + 买840×5.95g",
            "• 若非农弱+金价≤905 → 860买单自动成交, 可评估加5-10g",
            "• 若非农>150K → 撤860买单, 等CPI(8/12)",
            "",
            "⚠️ 纪律: r004数据前不新建>10%仓 | 不赌方向 | 让条件单回答",
        ])
    else:
        lines.extend([
            "🌆 【数据前 · 最后复查】",
            "今晚20:30 非农 (80k) 倒计时 1 小时, 检查:",
            "",
            "① 协议签署状态:",
            "   → 已签: 溢价回吐风险兑现, 860/840低吸接货",
            "   → 未签: 维持持有, 等非农",
            "② 条件单确认:",
            "   → OCO 950/852×15g 在挂 (机动仓10.57g<15g, 触发穿透核心仓4.43g)",
            "   → 买860×10g 在挂 (非农弱则成交)",
            "   → 买840×5.95g 在挂 (深水接货)",
            "③ 决策树:",
            "   → 非农<100K+价≤905: 860成交+可加5-10g",
            "   → 非农>150K: 撤860, 等CPI",
            "   → 协议签署+非农强(双杀): 840接货+评估",
            "",
            "⚠️ 卖出前3问: 纪律vs情绪? 空仓会买吗? 驱动链变了吗?",
        ])

    lines.extend([
        "",
        f"🤖 V7计划提醒 · {now.strftime('%H:%M')}",
    ])
    return "\n".join(lines)


def main() -> int:
    now = _now()
    # 仅 8/7 (周五) 运行
    if now.month != 8 or now.day != 7:
        return 0
    if now.weekday() >= 6:
        return 0

    price = _fetch_price_jd()
    portfolio = _get_portfolio()
    card = _nfp_card(now, price, portfolio)
    print(card, flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ V7非农提醒异常: {e}", file=sys.stderr)
        sys.exit(1)
