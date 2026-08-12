"""jdgold 模拟盘沙盒 — V9/L1 策略零风险验证 (本机, 需登录).

集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md E7。
模拟交易使用虚拟「金叶子」, 不涉及真实资金; 仅本机交互用, 不进 cron。

流程: 判登录 → 拉模拟K线+账户 → 现有策略栈 (ATR移动止盈 r025 + RSI/MA 低吸) →
输出买卖建议; execute=True 才真正下单 (幂等 bus_id)。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from gold_miner.data.jdgold_client import (
    check_login,
    fetch_sim_account,
    fetch_sim_kline,
    sim_buy,
    sim_sell,
)

_BEIJING = timezone(__import__("datetime").timedelta(hours=8))

# 低吸阈值 (与 adaptive_gold_monitor 同源)
_RSI_OVERSOLD = 30.0
_BUY_MA_WINDOW = 20


def _kline_to_df(kline_data: dict | None) -> pd.DataFrame | None:
    """模拟K线 raw → OHLCV DataFrame (防御式解析)."""
    if not kline_data:
        return None
    items = kline_data.get("items") or []
    if not isinstance(items, list) or not items:
        return None
    rows = []
    for it in items:
        close = _num(it.get("closePrice"))
        if close is None:
            continue
        date_str = it.get("tradeDate") or it.get("tradeTime") or ""
        rows.append({
            "timestamp": _parse_dt(date_str),
            "open": _num(it.get("openPrice")) or close,
            "high": _num(it.get("highPrice")) or close,
            "low": _num(it.get("lowPrice")) or close,
            "close": close,
            "volume": 0.0,
        })
    if not rows:
        return None
    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return df


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_dt(s: str) -> Any:
    import pandas as pd

    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return pd.to_datetime(datetime.strptime(s, fmt))
        except (ValueError, TypeError):
            continue
    return pd.Timestamp.now()


def _decide(
    atr_triggered: bool,
    atr_action: str,
    rsi14: float | None,
    ma20: float | None,
    current: float,
) -> tuple[str, str]:
    """买卖建议 (纯函数, 可测): ATR 止盈/止损优先 → 超卖低吸 → 观望."""
    if atr_triggered:
        action = "sell" if atr_action == "close_all" else "reduce"
        return action, f"ATR移动止盈触发 ({atr_action}), 现价 {current:.2f}"
    if rsi14 is not None and rsi14 < _RSI_OVERSOLD and (ma20 is None or current < ma20):
        return "buy", f"RSI(14)={rsi14:.0f} 超卖 + 现价低于MA20({ma20:.2f}), 低吸候选"
    rsi_str = f"{rsi14:.0f}" if rsi14 is not None else "—"
    return "hold", f"RSI(14)={rsi_str}, 现价 {current:.2f}, 观望"


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            avg_gain += d
        else:
            avg_loss -= d
    avg_gain /= period
    avg_loss /= period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


class SimSandboxEngine:
    """jdgold 模拟盘沙盒 — V9/L1 策略零风险验证.

    execute=False (默认) 只出建议; execute=True 才在模拟账户下单 (幂等 bus_id)。
    """

    def __init__(self, unique_code: str = "WG-JDAU", k_type: str = "day", nums: int = 60) -> None:
        self.unique_code = unique_code
        self.k_type = k_type
        self.nums = nums

    def evaluate(self, execute: bool = False) -> dict:
        """评估并 (可选) 执行模拟交易. 返回结构化结果."""
        logged_in, info = check_login()
        if not logged_in:
            return {
                "status": "not_logged_in",
                "message": "需要登录授权 (jdgold 模拟盘)",
                "reason": info.get("reason", "not_logged_in"),
            }

        kline = fetch_sim_kline(self.unique_code, self.k_type, self.nums)
        account = fetch_sim_account()
        if not kline or not account:
            return {"status": "error", "message": "模拟行情/账户获取失败 (需 jdgold 登录后重试)"}

        df = _kline_to_df(kline)
        if df is None or df.empty:
            return {"status": "error", "message": "模拟K线解析失败"}

        current = float(df["close"].iloc[-1])
        closes = df["close"].tolist()
        rsi14 = _rsi(closes)
        ma20 = sum(closes[-_BUY_MA_WINDOW:]) / _BUY_MA_WINDOW if len(closes) >= _BUY_MA_WINDOW else None

        # ATR 移动止盈 (r025) — 模拟账户成本均价作 cost_basis
        from gold_miner.strategy.trailing_stop import ATRTrailingStop

        cost_basis = _num(account.get("costAvgPerGram"))
        atr_sig = None
        try:
            atr_sig = ATRTrailingStop(
                atr_period=14, profit_multiplier=2.5, loss_multiplier=3.0,
                cost_basis=cost_basis,
            ).calculate(df)
        except Exception:
            pass

        atr_action = atr_sig.action if atr_sig else "hold"
        atr_stop = float(atr_sig.stop_price) if atr_sig else 0.0
        atr_triggered = bool(atr_sig and atr_sig.triggered)

        # 建议逻辑: ATR 止盈/止损 → 卖; 超卖 + 低于MA20 → 买; 否则观望
        recommendation, reason = _decide(atr_triggered, atr_action, rsi14, ma20, current)

        executed = None
        if execute and recommendation in ("buy", "reduce", "sell"):
            bus_id = f"sim_{int(time.time() * 1000)}"
            if recommendation == "buy":
                # 按金额买入 (模拟金叶子), 保守 1%
                trade_amount = round(float(account.get("availableAmount") or 0) * 0.01, 2)
                executed = sim_buy(trade_unit=1, bus_id=bus_id, trade_amount=trade_amount)
            else:
                # 按比例卖出持仓 50%
                executed = sim_sell(trade_unit=3, bus_id=bus_id, trade_ratio=0.5)
            executed = {"bus_id": bus_id, "resp": executed}

        return {
            "status": "ok",
            "current_price": round(current, 2),
            "rsi14": round(rsi14, 1) if rsi14 is not None else None,
            "ma20": round(ma20, 2) if ma20 is not None else None,
            "atr_stop": round(atr_stop, 2),
            "atr_triggered": atr_triggered,
            "account": {
                "available_amount": account.get("availableAmount"),
                "holding_gram": account.get("currentHoldingGram"),
                "cost_avg": account.get("costAvgPerGram"),
                "total_asset": account.get("totalAsset"),
            },
            "recommendation": recommendation,
            "reason": reason,
            "executed": executed,
        }

    def format_report(self, result: dict) -> str:
        """结构化结果 → 人话卡片 (微信/终端)."""
        if result.get("status") == "not_logged_in":
            return f"## 🎮 模拟盘沙盒\n需要登录授权。{result.get('reason', '')}"
        if result.get("status") == "error":
            return f"## 🎮 模拟盘沙盒\n{result.get('message', '')}"
        lines = [
            f"## 🎮 模拟盘沙盒 · {datetime.now(_BEIJING):%H:%M}",
            f"现价 **¥{result['current_price']:.2f}/克** | RSI14 {result['rsi14']} | MA20 ¥{result['ma20']}",
            f"ATR止盈位 ¥{result['atr_stop']:.2f} ({'🔴已触发' if result['atr_triggered'] else '未触发'})",
            f"建议: **{result['recommendation'].upper()}** — {result['reason']}",
        ]
        acc = result.get("account") or {}
        if acc.get("available_amount") is not None:
            lines.append(
                f"账户: 可用 {acc['available_amount']} 金叶子 | 持仓 {acc.get('holding_gram')}克 "
                f"| 成本 {acc.get('cost_avg')} | 总资产 {acc.get('total_asset')}"
            )
        if result.get("executed"):
            lines.append(f"✅ 已执行模拟下单 (bus_id {result['executed']['bus_id']})")
        lines.append("⚠️ 模拟交易不涉及真实资金, 仅供参考, 不构成投资建议")
        return "\n".join(lines)
