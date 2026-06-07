"""回测引擎."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import pandas as pd

from gold_miner.signals.base import SignalBundle


@dataclass
class BacktestResult:
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    profit_factor: float = 0.0
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.0005,
    ) -> None:
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct

    def run(
        self,
        price_df: pd.DataFrame,
        signal_fn: Callable[[pd.DataFrame], SignalBundle],
    ) -> BacktestResult:
        result = BacktestResult()
        capital = self.initial_capital
        position = 0.0
        entry_price = 0.0

        equity_curve: list[tuple[datetime, float]] = [(price_df["timestamp"].iloc[0], capital)]
        trades: list[dict[str, Any]] = []

        for i in range(50, len(price_df)):
            window = price_df.iloc[:i]
            current = price_df.iloc[i]
            current_time = current["timestamp"]
            current_price = current["close"]

            bundle = signal_fn(window)
            score = bundle.composite_score

            if score > 0.3 and position == 0:
                entry_price = current_price * (1 + self.slippage_pct)
                position_size = min(score * 0.8, 0.8)
                position = position_size
                trades.append({"time": current_time, "action": "buy", "price": entry_price, "size": position_size})

            elif score < -0.3 and position > 0:
                exit_price = current_price * (1 - self.slippage_pct)
                pnl = (exit_price - entry_price) / entry_price * position
                capital *= (1 + pnl - self.commission_pct)
                trades.append({"time": current_time, "action": "sell", "price": exit_price, "pnl": pnl})
                position = 0.0
                entry_price = 0.0

            current_equity = capital
            if position > 0:
                unrealized = (current_price - entry_price) / entry_price * position
                current_equity = capital * (1 + unrealized)

            equity_curve.append((current_time, current_equity))

        if position > 0:
            last_price = price_df["close"].iloc[-1]
            pnl = (last_price - entry_price) / entry_price * position
            capital *= (1 + pnl - self.commission_pct)
            equity_curve[-1] = (equity_curve[-1][0], capital)

        return self._calculate_metrics(equity_curve, trades)

    def _calculate_metrics(
        self,
        equity_curve: list[tuple[datetime, float]],
        trades: list[dict[str, Any]],
    ) -> BacktestResult:
        result = BacktestResult()
        if len(equity_curve) < 2:
            return result

        values = [e[1] for e in equity_curve]
        result.equity_curve = equity_curve
        result.total_return = (values[-1] - values[0]) / values[0]

        days = (equity_curve[-1][0] - equity_curve[0][0]).days
        if days > 0:
            result.annual_return = (1 + result.total_return) ** (365 / days) - 1

        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = max_dd

        closed_trades = [t for t in trades if t.get("action") == "sell"]
        result.total_trades = len(closed_trades)
        if closed_trades:
            wins = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
            result.win_rate = wins / len(closed_trades)
            profits = sum(t["pnl"] for t in closed_trades if t.get("pnl", 0) > 0)
            losses = sum(abs(t["pnl"]) for t in closed_trades if t.get("pnl", 0) <= 0)
            result.profit_factor = profits / losses if losses > 0 else float("inf")

        returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]
        if returns:
            mean_ret = sum(returns) / len(returns)
            std_ret = (sum((r - mean_ret) ** 2 for r in returns) / len(returns)) ** 0.5
            if std_ret > 0:
                result.sharpe_ratio = mean_ret / std_ret * (252 ** 0.5)

        result.trades = trades
        return result
