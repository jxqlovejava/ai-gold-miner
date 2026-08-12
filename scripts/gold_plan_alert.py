#!/usr/bin/env python3
"""黄金计划级提醒 — L1 / V9 / L1-A 触发条件监控.

Hermes cron --no-agent --script 模式. stdout 直接投递微信, 空 stdout = 静默.

与 adaptive_gold_monitor.py 的分工:
  - adaptive (每分钟) 覆盖: ATR止损/硬止损/急涨急跌/机会(止盈抄底) — 价格级
  - 本脚本 (工作日盘中 N 次) 覆盖: 计划级触发 — L1/V9/L1-A 引擎状态、试盘/加仓点、
    军规突破档激活/降级、资金流闸门、浮盈>20%止损上移(r010)

设计要点 (2026-08-11):
  - 聚焦 adaptive 未覆盖的计划级逻辑, 避免重复推送
  - 冷却: 同类型提醒 12h 内不重复 (STATE_FILE 记录)
  - 空 stdout 静默; 仅条件满足时输出微信卡片
  - 休息日 (周末) 静默 — 积存金休市

Hermes 约定:
  - stdout 打印提醒卡片 → hermes 投递微信
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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = Path(os.environ.get("GOLD_PORTFOLIO", "/home/ubuntu/.hermes/gold/portfolio.yaml"))
ORDERS = Path(os.environ.get("GOLD_ORDERS", "/home/ubuntu/.hermes/gold/conditional_orders.jsonl"))
STATE_FILE = Path(os.environ.get("GOLD_PLAN_STATE", "/home/ubuntu/.hermes/gold/plan_alert_state.json"))

# L1 关键点 (2026-08-11 L1/V9 文档)
KEY_LEVEL_BREAK = 4500.0   # XAUUSD 结构牛第三关: 破 $4,500 站稳
KEY_LEVEL_INVALID = 4400.0  # 三关失守: 跌破 $4,400 → 加速档降级
GOLD_JD_BREAK = 950.0      # 积存金 $4,500 对应位 ≈ ¥950

# 冷却: 同类型提醒 N 秒内不重复 (12h)
_COOLDOWN_SEC = 12 * 3600

# 浮盈止损上移阈值 (r010)
_PROFIT_TRAIL_PCT = 20.0


def _now() -> datetime:
    return datetime.now(BEIJING)


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _in_cooldown(state: dict, key: str) -> bool:
    last = state.get("last_alert", {}).get(key)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        return (_now() - last_dt).total_seconds() < _COOLDOWN_SEC
    except Exception:
        return False


def _mark_alerted(state: dict, key: str) -> None:
    state.setdefault("last_alert", {})[key] = _now().isoformat()
    _save_state(state)


def _fetch_price_jd() -> dict | None:
    """获取积存金当前价 — jdgold 主源 → latestPrice H5 兜底 (收口至 jdgold_client)."""
    from gold_miner.data.jdgold_client import fetch_accumulation_quote

    return fetch_accumulation_quote()


def _fetch_xauusd() -> float | None:
    """获取 XAUUSD 参考价 — 用于判定结构牛三关 ($4,500).

    优先从监控状态文件读取(自适应监控缓存的最新国际价), 无则尝试 API.
    失败时返回 None → 引擎状态判定降级为「未知」.
    """
    try:
        # 尝试读取自适应监控缓存的 XAUUSD
        cache = Path("/home/ubuntu/.hermes/gold/adaptive_monitor_state.json")
        if cache.exists():
            d = json.loads(cache.read_text())
            xau = d.get("xauusd") or d.get("last_xauusd")
            if xau:
                return float(xau)
    except Exception:
        pass
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
        profit_pct = (grams and 0.0)  # 占位
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


def _engine_status(xau: float | None, jd: float | None) -> dict:
    """判定 L1 引擎状态 + 距离关键点.

    三关: ①站上短均线(由外部信号判定, 此处用 XAUUSD 代理) ②破关键阻力 ③破 $4,500.
    简化代理: xau ≥ 4500 → 三关齐(结构牛确认); xau < 4400 → 失守降级.
    """
    if xau is None and jd is None:
        return {"state": "未知", "note": "无法获取国际/国内价", "distance_pct": None}

    # 用可用价格判定 (优先 XAUUSD, 国内价按换算参考)
    if xau is not None:
        price_ref = xau
        if price_ref >= KEY_LEVEL_BREAK:
            return {"state": "结构牛确认", "note": f"XAUUSD {price_ref:.0f} ≥ $4,500", "distance_pct": 0.0}
        if price_ref < KEY_LEVEL_INVALID:
            return {"state": "降级", "note": f"XAUUSD {price_ref:.0f} < $4,400 三关失守", "distance_pct": None}
        dist = (KEY_LEVEL_BREAK - price_ref) / KEY_LEVEL_BREAK * 100
        return {"state": "观察期", "note": f"XAUUSD {price_ref:.0f}, 距 $4,500 {dist:.1f}%", "distance_pct": dist}

    # 仅国内价
    if jd is not None:
        if jd >= GOLD_JD_BREAK:
            return {"state": "结构牛确认", "note": f"积存金 ¥{jd:.0f} ≥ ¥950", "distance_pct": 0.0}
        dist = (GOLD_JD_BREAK - jd) / GOLD_JD_BREAK * 100
        return {"state": "观察期", "note": f"积存金 ¥{jd:.0f}, 距 ¥950 {dist:.1f}%", "distance_pct": dist}

    return {"state": "未知", "note": "数据不足", "distance_pct": None}


def _profit_trail_check(portfolio: dict | None, price: float) -> dict | None:
    """r010: 浮盈 >20% → 提醒止损上移成本上方. adaptive 未覆盖, 本脚本负责."""
    if not portfolio:
        return None
    try:
        pos = portfolio["positions"]["gold_jd"]
        avg_cost = float(pos["avg_cost"])
        sell_fee = float(pos.get("sell_fee_pct", 0.4))
        net_breakeven = avg_cost / (1 - sell_fee / 100)
        profit_pct = (price - avg_cost) / avg_cost * 100
        if profit_pct >= _PROFIT_TRAIL_PCT:
            return {
                "profit_pct": profit_pct,
                "avg_cost": avg_cost,
                "net_breakeven": net_breakeven,
                "action": "浮盈>20%, 止损必须上移至成本价上方(r010), 防止利润大幅回吐",
            }
    except Exception:
        pass
    return None


def _build_engine_card(now: datetime, price: dict | None, jd: float | None,
                       portfolio: str | None, orders: str, status: dict) -> str:
    lines = [
        f"📊 L1/V9 引擎状态 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    lines.append(f"🧭 结构牛状态: {status['state']} — {status['note']}")
    if portfolio:
        lines.append(f"📦 持仓: {portfolio}")
    lines.append(f"🔖 条件单: {orders}")
    lines.append("")
    lines.append("💡 L1 引擎: 观察期→只试盘 / 结构牛确认→递增加仓")
    lines.append("   L1-A 突破档: 需 $4,500 站稳≥5日 + 资金流同向≥2周才激活")
    lines.append("   三关失守(<$4,400) → 加速仓减回常规70%档")
    lines.append("")
    lines.append(f"🤖 L1/V9 计划提醒 · {now.strftime('%H:%M')}")
    return "\n".join(lines)


def _build_breakout_card(now: datetime, price: dict | None, status: dict) -> str:
    lines = [
        f"🚀 结构牛突破信号 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    lines.append(f"🧭 状态: {status['state']} — {status['note']}")
    lines.append("")
    lines.append("🎯 L1-A 加速档触发条件: 破 $4,500 + 回踩不破 + 资金流同向")
    lines.append("   突破档(可破 r002 80%): 需站稳≥5日 + 资金流同向≥2周")
    lines.append("   激活动作: 核心池建满45% + S协议评估 + L1放大档")
    lines.append("")
    lines.append("⚠️ 军规突破前提: 全仓位 ATR 止损常挂 + 现金≥10% + 三关失守自动退回")
    lines.append("")
    lines.append(f"🤖 结构牛突破提醒 · {now.strftime('%H:%M')}")
    return "\n".join(lines)


def _build_degrade_card(now: datetime, price: dict | None, status: dict) -> str:
    lines = [
        f"🔻 结构牛三关失守 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    lines.append(f"🧭 状态: {status['state']} — {status['note']}")
    lines.append("")
    lines.append("⚠️ 加速/突破档仓位须减回常规 70% 档 (30日证伪撤销)")
    lines.append("   核心池继续低吸, 机动池转波段")
    lines.append("")
    lines.append(f"🤖 结构牛降级提醒 · {now.strftime('%H:%M')}")
    return "\n".join(lines)


def _build_profit_card(now: datetime, price: dict | None, check: dict) -> str:
    lines = [
        f"💸 浮盈止损上移提醒 | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    lines.append(f"📈 浮盈 {check['profit_pct']:.1f}% (成本 ¥{check['avg_cost']:.2f})")
    lines.append(f"   → {check['action']}")
    lines.append("")
    lines.append(f"🤖 r010 止损上移提醒 · {now.strftime('%H:%M')}")
    return "\n".join(lines)


# ── 缺口2: S档快止损持续推送 (2026-08-11) ──
# S协议 fast_stop = 成本 -8%. 用户明确要求: 止损提醒不设冷却, 持续推送直到看到并处理.
# 机制: 跌破止损位 → 每 _FAST_STOP_INTERVAL_MIN 分钟持续推送, 直到价格回到位上方.
#   - 首次触发立即推送; 之后每 15 分钟重复 (用户要求: 需要处理就重复提醒, 不能用冷却压掉)
#   - 价格回到位上方 → 自动停止 (客观解除, 无需用户操作)
# 与 gold_stop_level_alert.py 的"四级止损提醒"互补: 那个覆盖 -5%/-10%/-30%;
# 本脚本补上 S 档专属的 -8% 快止损 + 持续升级告警.

_FAST_STOP_INTERVAL_MIN = 15  # 跌破止损位后, 每 N 分钟持续推送一次 (用户要求: 直到处理)
# S档快止损 = 成本 - fast_stop_pct% (fast_stop_pct 来自 portfolio s_protocol)
_DEFAULT_FAST_STOP_PCT = 8.0


def _fast_stop_level(portfolio: dict | None) -> float | None:
    """S 档快止损位 = 成本 × (1 - fast_stop_pct/100)."""
    if not portfolio:
        return None
    try:
        pos = portfolio["positions"]["gold_jd"]
        avg_cost = float(pos["avg_cost"])
        fast_pct = float(
            portfolio.get("long_term", {}).get("s_protocol", {}).get("fast_stop_pct", _DEFAULT_FAST_STOP_PCT)
        ) or _DEFAULT_FAST_STOP_PCT
        return round(avg_cost * (1 - fast_pct / 100), 2)
    except Exception:
        return None


def _check_fast_stop_escalation(state: dict, portfolio: dict | None, jd: float) -> dict | None:
    """S 档快止损持续推送: 现价 ≤ 成本-8% 时, 每 _FAST_STOP_INTERVAL_MIN 分钟推一次.

    停止条件 (任一):
      1. 现价回到止损位上方 (客观解除, 无需用户操作)
      2. 用户已处理 (未来接入确认通道后可判 ack)
    不设冷却上限 — 用户明确要求"需要处理就重复提醒, 直到看到并处理了".

    Returns: 告警卡片 dict 或 None (未跌破位 / 未到推送间隔).
    """
    level = _fast_stop_level(portfolio)
    if level is None or jd > level:
        # 价格回到位上方 → 清除推送状态, 下次跌破重新从首次触发开始 (OCR#1)
        if state.get("last_alert", {}).pop("fast_stop", None) is not None:
            _save_state(state)
        return None  # 未跌破位 → 静默
    last = state.get("last_alert", {}).get("fast_stop")
    if last is None:
        # 首次触发 → 立即推送 (escalated=False 首次提示)
        return {"level": level, "escalated": False, "last": None}
    try:
        last_dt = datetime.fromisoformat(last)
        age_min = (_now() - last_dt).total_seconds() / 60
    except Exception:
        # state 时间戳损坏 → 按首次触发起步, 不误判为持续提醒 (OCR#2)
        return {"level": level, "escalated": False, "last": None}
    # 已推送过 → 每 _FAST_STOP_INTERVAL_MIN 分钟持续重复 (升级提示), 直到处理
    if age_min < _FAST_STOP_INTERVAL_MIN:
        return None  # 未到下一次推送间隔
    return {"level": level, "escalated": True, "last": last}


def _build_fast_stop_card(now: datetime, price: dict | None, level: float, escalated: bool) -> str:
    head = "🔴🔴 S档快止损·持续提醒" if escalated else "🔴 S档快止损触发"
    lines = [
        f"{head} | {now.strftime('%m/%d %H:%M')}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    if price:
        lines.append(f"💰 积存金: ¥{price['price']:.2f} ({price['change_pct']:+.2f}%)")
    lines.append(f"🚨 已跌破 S 档快止损位 ¥{level:.2f} (成本 -8%)")
    if escalated:
        lines.append("⏰ 每 15 分钟持续提醒中 — 处理前会一直推送:")
    lines.append("   → S 协议机会仓 -8% 快止损, 不拖到 -30% (r014)")
    lines.append("   → 若持仓为机会池激进仓: 无条件减仓/清仓")
    lines.append("   → 若为核心仓: 检查 ATR 移动止盈位是否已触发")
    lines.append("")
    lines.append("💡 处理完或金价回到止损位上方后, 提醒自动停止")
    lines.append("")
    lines.append(f"🤖 S档止损告警 · {now.strftime('%H:%M')}")
    return "\n".join(lines)


def main() -> int:
    now = _now()
    if now.weekday() >= 5:
        return 0  # 周六日积存金休市

    state = _load_state()

    # 1. 拉数据
    price = _fetch_price_jd()
    jd = price["price"] if price else None
    xau = _fetch_xauusd()
    portfolio = _load_portfolio()

    # 2. 引擎状态判定
    status = _engine_status(xau, jd)
    state_key = "engine:" + status["state"]

    # 3. 引擎状态变化提醒 (非冷却 + 状态非「未知」)
    emitted = []
    if status["state"] not in ("未知", "观察期"):
        if not _in_cooldown(state, state_key):
            if status["state"] == "结构牛确认":
                emitted.append(_build_breakout_card(now, price, status))
            elif status["state"] == "降级":
                emitted.append(_build_degrade_card(now, price, status))
            _mark_alerted(state, state_key)
    elif status["state"] == "观察期":
        # 每日首次观察期状态同步(无冷却, 低频 run 天然限频)
        if not state.get("last_engine_sync") or \
           (_now() - datetime.fromisoformat(state["last_engine_sync"])).total_seconds() > _COOLDOWN_SEC:
            emitted.append(_build_engine_card(now, price, jd, _portfolio_snapshot(portfolio), _active_orders(), status))
            state["last_engine_sync"] = _now().isoformat()
            _save_state(state)

    # 4. r010 浮盈止损上移提醒 (adaptive 未覆盖)
    if jd and portfolio:
        profit_check = _profit_trail_check(portfolio, jd)
        if profit_check and not _in_cooldown(state, "profit_trail"):
            emitted.append(_build_profit_card(now, price, profit_check))
            _mark_alerted(state, "profit_trail")

    # 4b. S 档快止损二次告警 (缺口2: -8%触发 + 30min未确认升级)
    if jd and portfolio:
        fast_check = _check_fast_stop_escalation(state, portfolio, jd)
        if fast_check is not None:
            emitted.append(
                _build_fast_stop_card(now, price, fast_check["level"], fast_check["escalated"])
            )
            _mark_alerted(state, "fast_stop")

    # 5. 输出 (空 = 静默)
    for card in emitted:
        print(card, flush=True)
        print("", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 计划级提醒异常: {e}", file=sys.stderr)
        sys.exit(1)
