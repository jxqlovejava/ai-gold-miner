#!/usr/bin/env python3
"""盈利保护与聪明钱撤退监控 — 每5分钟轮询, 检测获利盘出逃信号 → Hermes → 个人微信.

覆盖四个维度:
  1. 成本逼近预警 — 价格从上方逼近成本线, 在盈利缩水前提醒减仓
  2. 高点回撤检测 — 距N日高点跌幅超过阈值, 获利盘出逃信号
  3. 连续下跌确认 — 连续N日收阴, 趋势反转
  4. 日内逆转检测 — 当日从涨转跌, 盘中出货

Hermes 约定:
  - 无预警: stdout 为空, exit 0
  - 有预警: stdout 打印人话卡片, exit 0
  - 致命错误: stderr 打印, exit 1

用法:
  PYTHONPATH=src python3 scripts/profit_protection_monitor.py

cron (北京时间 9:05-23:55 每5分钟, Mon-Fri):
  */5 9-23 * * 1-5 cd /path/to/ai-gold-miner && PYTHONPATH=src python3 scripts/profit_protection_monitor.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))

# ── 配置 ──
# 成本逼近: 当前价距成本线在以下范围内触发预警
COST_PROXIMITY_BANDS = [
    (0.03, "🔴 仅剩3%盈利! 距成本线一步之遥, 建议减仓50%保护本金"),
    (0.02, "🚨 仅剩2%盈利! 获利盘快速出逃, 建议减仓70%锁定微利"),
    (0.01, "💀 仅剩1%盈利! 即将亏损, 建议清仓或设保本止损"),
    (0.00, "❌ 跌破成本线! 当前已浮亏, 严格执行止损纪律"),
]
# 高点回撤: 距N日高点跌幅阈值
PEAK_DRAWDOWN_DAYS = [3, 5, 7, 14]  # 观察多个窗口
PEAK_DRAWDOWN_THRESHOLDS = [
    (0.03, "⚠️ 距高点回撤3%, 短线获利盘开始出逃"),
    (0.05, "🔶 距高点回撤5%, 中线资金在撤退, 考虑减仓"),
    (0.08, "🔴 距高点回撤8%, 趋势可能反转, 建议大幅减仓"),
]
# 连续下跌天数
CONSECUTIVE_DOWN_DAYS = 3  # 连续N日收阴触发
# 日内逆转: 当日从涨转跌幅度
INTRADAY_REVERSAL_PCT = 1.5  # 从日内高点到当前价跌幅 > 1.5%
# 冷却: 同一方向预警间隔
COOLDOWN_MINUTES = 30
# 状态文件
STATE_FILE = Path(os.environ.get(
    "PROFIT_PROTECTOR_STATE",
    os.path.expanduser("~/.hermes/gold/profit_protector_state.json"),
))
# 持仓文件
PORTFOLIO_PATH = Path(os.environ.get(
    "GOLD_PORTFOLIO_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "data/private/portfolio.yaml"),
))


def _now() -> datetime:
    return datetime.now(BEIJING)


# ── 数据获取 ──

def _fetch_price() -> dict | None:
    """获取积存金当前价 — jdgold 主源 → latestPrice H5 兜底 (收口至 jdgold_client)."""
    from gold_miner.data.jdgold_client import fetch_accumulation_quote

    return fetch_accumulation_quote()


def _load_portfolio() -> dict | None:
    """读取持仓文件."""
    if not PORTFOLIO_PATH.exists():
        return None
    try:
        import yaml
        with open(PORTFOLIO_PATH) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _get_cost_basis() -> float | None:
    """获取成本均价; 空仓 (grams<=0) 返回 None.

    2026-08-21 修复: 清仓后 avg_cost 仍保留作历史参考, 只读它会把空仓误判为有仓.
    """
    p = _load_portfolio()
    if not p:
        return None
    try:
        pos = p["positions"]["gold_jd"]
        if float(pos.get("grams", 0) or 0) <= 0:
            return None
        return float(pos["avg_cost"])
    except (KeyError, ValueError, TypeError):
        return None


def _get_historical_prices(days: int = 30) -> list[dict]:
    """获取积存金历史价格序列."""
    try:
        from gold_miner.data.jd_accumulation_gold import JdAccumulationGoldFetcher
        f = JdAccumulationGoldFetcher(bank="MS")
        df = f.fetch(days=days)
        if df.empty:
            return []
        prices = []
        for _, row in df.iterrows():
            prices.append({
                "date": str(row["timestamp"].date()) if hasattr(row["timestamp"], "date") else str(row["timestamp"])[:10],
                "close": float(row["close"]),
            })
        return prices
    except Exception:
        return []


# ── 状态管理 ──

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── 检测逻辑 ──

def _check_cost_proximity(current_price: float, cost_basis: float) -> dict | None:
    """检测成本逼近 — 价格从上方逼近成本线."""
    if current_price <= cost_basis:
        # 已在成本之下
        loss_pct = (cost_basis - current_price) / cost_basis * 100
        return {
            "type": "cost_proximity",
            "level": "below_cost",
            "price": current_price,
            "cost": cost_basis,
            "gap_pct": -loss_pct,
            "gap_yuan": current_price - cost_basis,
            "message": f"❌ 跌破成本线 {cost_basis:.2f}元! 当前 {current_price:.2f}, "
                       f"浮亏 {loss_pct:.1f}% ({cost_basis - current_price:.2f}元/克)",
        }

    # 价格在成本之上，检测逼近程度
    profit_margin = (current_price - cost_basis) / cost_basis
    for threshold_pct, message in COST_PROXIMITY_BANDS:
        if profit_margin <= threshold_pct:
            return {
                "type": "cost_proximity",
                "level": f"within_{threshold_pct*100:.0f}pct",
                "price": current_price,
                "cost": cost_basis,
                "gap_pct": profit_margin * 100,
                "gap_yuan": current_price - cost_basis,
                "message": message.replace("成本线", f"{cost_basis:.2f}元成本线"),
            }

    return None  # 盈利空间充足


def _check_peak_drawdown(current_price: float, historical: list[dict]) -> dict | None:
    """检测距近期高点的回撤幅度 — 获利盘出逃信号."""
    if len(historical) < 3:
        return None

    alerts = []
    for window_days in PEAK_DRAWDOWN_DAYS:
        if len(historical) < window_days:
            continue
        window = historical[-window_days:]
        peak = max(p["close"] for p in window)
        drawdown = (peak - current_price) / peak

        for threshold_pct, message in PEAK_DRAWDOWN_THRESHOLDS:
            if drawdown >= threshold_pct:
                alerts.append({
                    "type": "peak_drawdown",
                    "window_days": window_days,
                    "peak": peak,
                    "current": current_price,
                    "drawdown_pct": drawdown * 100,
                    "threshold_pct": threshold_pct * 100,
                    "message": f"{message} | {window_days}日高点 {peak:.0f}元 → 当前 {current_price:.0f}元 ({drawdown*100:.1f}%)",
                })
                break  # 同一窗口只取最高阈值

    # 返回最近窗口的最高严重级别
    if alerts:
        return alerts[0]
    return None


def _check_consecutive_down(historical: list[dict]) -> dict | None:
    """检测连续下跌天数."""
    if len(historical) < CONSECUTIVE_DOWN_DAYS + 1:
        return None

    closes = [p["close"] for p in historical]
    down_count = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            down_count += 1
        else:
            break

    if down_count >= CONSECUTIVE_DOWN_DAYS:
        total_change = (closes[-1] - closes[-1 - down_count]) / closes[-1 - down_count] * 100
        return {
            "type": "consecutive_down",
            "days": down_count,
            "from_price": closes[-1 - down_count],
            "to_price": closes[-1],
            "change_pct": total_change,
            "message": f"📉 连续{down_count}日下跌! "
                       f"从 {closes[-1-down_count]:.0f} → {closes[-1]:.0f}元 "
                       f"({total_change:+.1f}%), 趋势转弱, 考虑减仓",
        }
    return None


def _check_intraday_reversal(current_price: float, prev_close: float,
                              change_pct: float) -> dict | None:
    """检测日内逆转 — 从上涨转为大幅下跌."""
    # 需要当日开盘价, 用 prev_close 近似
    # 核心逻辑: 当前跌幅 > 阈值 且 绝对值可观
    if change_pct <= -INTRADAY_REVERSAL_PCT:
        return {
            "type": "intraday_reversal",
            "prev_close": prev_close,
            "current": current_price,
            "change_pct": change_pct,
            "message": f"⚡ 日内急跌! 昨收 {prev_close:.0f} → 当前 {current_price:.0f} "
                       f"({change_pct:+.1f}%), 盘中获利盘集中出逃",
        }
    return None


def _in_cooldown(last_alert_at: str | None, alert_type: str) -> bool:
    """检查预警冷却."""
    if not last_alert_at:
        return False
    try:
        last_at = datetime.fromisoformat(last_alert_at)
        elapsed = (_now() - last_at).total_seconds() / 60
        return elapsed < COOLDOWN_MINUTES
    except (ValueError, TypeError):
        return False


# ── 格式化 ──

def _format_alert(alerts: list[dict], price_info: dict, cost_basis: float | None) -> str:
    """格式化预警为人话卡片."""
    now = _now()
    lines = [
        f"🛡️ 盈利保护预警 | {now.strftime('%m-%d %H:%M')}",
        "",
        f"💰 当前金价: {price_info['price']:.2f}元/克 ({price_info['change_pct']:+.2f}%)",
    ]

    if cost_basis:
        pnl = (price_info["price"] - cost_basis) / cost_basis * 100
        pnl_emoji = "🟢" if pnl > 0 else "🔴"
        lines.append(f"📊 成本均价: {cost_basis:.2f}元/克 | 盈亏: {pnl_emoji} {pnl:+.1f}%")
    lines.append("")

    for alert in alerts:
        lines.append(f"━━━ {alert['type'].replace('_', ' ').title()} ━━━")
        lines.append(alert["message"])
        lines.append("")

    # 建议汇总
    lines.append("━━━ 💡 建议动作 ━━━")
    has_cost_below = any(a["type"] == "cost_proximity" and a.get("level") == "below_cost"
                         for a in alerts)
    has_cost_close = any(a["type"] == "cost_proximity" and a.get("level", "").startswith("within")
                         for a in alerts)
    has_drawdown = any(a["type"] == "peak_drawdown" for a in alerts)
    has_down_days = any(a["type"] == "consecutive_down" for a in alerts)
    has_reversal = any(a["type"] == "intraday_reversal" for a in alerts)

    if has_cost_below:
        lines.append("• 🔴 已跌破成本, 严格执行止损纪律")
        lines.append("• 检查条件单: 止损单是否有效")
    elif has_cost_close:
        lines.append("• 🟡 盈利空间快速收窄, 考虑减仓保护")
        lines.append("• 建议: 卖出一半仓位锁定微利, 留一半设保本止损")
    if has_drawdown:
        lines.append("• 🟠 高点回撤信号, 获利盘在出逃")
        lines.append("• 建议: 若回撤>5%且无明确利好, 减仓至50%以下")
    if has_down_days:
        lines.append("• 🟠 连续下跌趋势, 不宜加仓")
        lines.append("• 建议: 等待止跌企稳信号再操作")
    if has_reversal:
        lines.append("• 🔴 盘中急跌, 可能有重大利空")
        lines.append("• 建议: 先减仓观察, 等消息明朗再决定")

    return "\n".join(lines)


# ── 主入口 ──

def main() -> int:
    # 1. 获取价格
    price_info = _fetch_price()
    if not price_info:
        return 0  # 网络不可用, 静默

    current = price_info["price"]
    prev_close = price_info["prev_close"]
    change_pct = price_info["change_pct"]

    # 2. 获取成本
    cost_basis = _get_cost_basis()

    # 3. 获取历史
    historical = _get_historical_prices(days=30)

    # 4. 状态
    state = _load_state()

    # 5. 逐一检测
    alerts: list[dict] = []

    # 成本逼近 (只在有成本数据时)
    if cost_basis:
        cost_alert = _check_cost_proximity(current, cost_basis)
        if cost_alert and not _in_cooldown(
            state.get(f"cooldown_{cost_alert['type']}_{cost_alert.get('level', '')}"),
            cost_alert["type"],
        ):
            alerts.append(cost_alert)

    # 高点回撤
    peak_alert = _check_peak_drawdown(current, historical)
    if peak_alert and not _in_cooldown(
        state.get(f"cooldown_peak_{peak_alert['window_days']}d"),
        "peak_drawdown",
    ):
        alerts.append(peak_alert)

    # 连续下跌
    down_alert = _check_consecutive_down(historical)
    if down_alert and not _in_cooldown(
        state.get("cooldown_consecutive_down"),
        "consecutive_down",
    ):
        alerts.append(down_alert)

    # 日内逆转
    reversal_alert = _check_intraday_reversal(current, prev_close, change_pct)
    if reversal_alert and not _in_cooldown(
        state.get("cooldown_intraday_reversal"),
        "intraday_reversal",
    ):
        alerts.append(reversal_alert)

    # 6. 更新冷却状态
    for alert in alerts:
        key = f"cooldown_{alert['type']}"
        if alert["type"] == "peak_drawdown":
            key = f"cooldown_peak_{alert.get('window_days', 0)}d"
        elif alert["type"] == "cost_proximity":
            key = f"cooldown_{alert['type']}_{alert.get('level', '')}"
        state[key] = _now().isoformat()

    _save_state(state)

    # 7. 输出
    if alerts:
        print(_format_alert(alerts, price_info, cost_basis), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 盈利保护监控异常: {e}", file=sys.stderr)
        sys.exit(1)
