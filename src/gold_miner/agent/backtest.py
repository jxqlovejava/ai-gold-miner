"""历史回测框架 — 验证策略和信号的历史表现."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class BacktestResult:
    """回测结果."""
    name: str
    start_date: str
    end_date: str
    total_trades: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    annual_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0
    trades: list[dict] = field(default_factory=list)
    signal_accuracy: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"回测: {self.name}",
            f"期间: {self.start_date} → {self.end_date}",
            f"交易次数: {self.total_trades}  |  胜率: {self.win_rate:.1%}",
            f"总收益: {self.total_return_pct:+.2f}%  |  年化: {self.annual_return_pct:+.2f}%",
            f"最大回撤: {self.max_drawdown_pct:.2f}%  |  夏普: {self.sharpe_ratio:.2f}",
            f"平均盈利: {self.avg_win_pct:+.2f}%  |  平均亏损: {self.avg_loss_pct:+.2f}%",
            f"盈亏比: {self.profit_factor:.2f}",
        ]
        return "\n".join(lines)


class BacktestEngine:
    """回测引擎 — 验证信号策略.

    使用方式:
        engine = BacktestEngine()
        result = engine.run_simple_signal(
            gold_df, signal_fn=lambda df: df['close'].pct_change(5) > 0.02
        )
    """

    def __init__(self, data_dir: str = "data/backtests") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 简单信号回测
    # ------------------------------------------------------------------

    def run_simple_signal(
        self,
        gold_df: pd.DataFrame,
        signal_fn,
        name: str = "simple_signal",
        initial_capital: float = 100_000,
        position_pct: float = 1.0,
        commission: float = 0.001,
    ) -> BacktestResult:
        """回测单一信号.

        signal_fn: 接收 (gold_df, idx) 返回 1(buy)/-1(sell)/0(hold)
        """
        if gold_df.empty or len(gold_df) < 20:
            return BacktestResult(name=name, start_date="", end_date="")

        df = gold_df.reset_index(drop=True)
        closes = df["close"].values
        capital = initial_capital
        position = 0.0  # 当前持仓克数
        peak_capital = initial_capital
        trades: list[dict] = []
        daily_values: list[float] = []

        for i in range(len(df)):
            signal = signal_fn(df, i) if callable(signal_fn) else 0

            # 平仓 (反向信号)
            if position > 0 and signal < 0:
                trade_return = position * closes[i] - position * closes[i - 1]
                trades.append({
                    "date": str(df.iloc[i].get("timestamp", i)),
                    "action": "sell",
                    "price": closes[i],
                    "pnl": trade_return,
                })
                capital += position * closes[i] * (1 - commission)
                position = 0

            # 开仓
            elif position == 0 and signal > 0:
                invest = capital * position_pct
                position = invest / closes[i] * (1 - commission)
                trades.append({
                    "date": str(df.iloc[i].get("timestamp", i)),
                    "action": "buy",
                    "price": closes[i],
                    "pnl": 0,
                })
                capital -= invest

            # 更新市值
            current_value = capital + position * closes[i]
            daily_values.append(current_value)
            if current_value > peak_capital:
                peak_capital = current_value

        # 强制平仓
        if position > 0:
            capital += position * closes[-1] * (1 - commission)
            position = 0

        return self._calc_metrics(
            name=name,
            start_date=str(df.iloc[0].get("timestamp", "")),
            end_date=str(df.iloc[-1].get("timestamp", "")),
            trades=trades,
            daily_values=daily_values,
            initial_capital=initial_capital,
        )

    # ------------------------------------------------------------------
    # 技术指标回测
    # ------------------------------------------------------------------

    def run_ma_crossover(
        self, gold_df: pd.DataFrame, fast: int = 5, slow: int = 20,
    ) -> BacktestResult:
        """均线金叉/死叉策略回测."""
        df = gold_df.copy()
        df["ma_fast"] = df["close"].rolling(fast).mean()
        df["ma_slow"] = df["close"].rolling(slow).mean()

        def signal_fn(data: pd.DataFrame, i: int) -> int:
            if i < slow:
                return 0
            # 金叉
            if data["ma_fast"].iloc[i] > data["ma_slow"].iloc[i] and \
               data["ma_fast"].iloc[i - 1] <= data["ma_slow"].iloc[i - 1]:
                return 1
            # 死叉
            if data["ma_fast"].iloc[i] < data["ma_slow"].iloc[i] and \
               data["ma_fast"].iloc[i - 1] >= data["ma_slow"].iloc[i - 1]:
                return -1
            return 0

        return self.run_simple_signal(df, signal_fn, f"MA_{fast}_{slow}_crossover")

    def run_rsi_strategy(
        self, gold_df: pd.DataFrame, period: int = 14,
        oversold: float = 30, overbought: float = 70,
    ) -> BacktestResult:
        """RSI超买超卖策略回测."""
        df = gold_df.copy()
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, 1)
        df["rsi"] = 100 - (100 / (1 + rs))

        def signal_fn(data: pd.DataFrame, i: int) -> int:
            if i < period:
                return 0
            rsi = data["rsi"].iloc[i]
            rsi_prev = data["rsi"].iloc[i - 1]
            if rsi_prev <= oversold and rsi > oversold:
                return 1
            if rsi_prev >= overbought and rsi < overbought:
                return -1
            return 0

        return self.run_simple_signal(df, signal_fn, f"RSI_{period}_{oversold}_{overbought}")

    def run_buy_and_hold(
        self, gold_df: pd.DataFrame,
    ) -> BacktestResult:
        """买入持有基准策略."""
        df = gold_df.reset_index(drop=True)
        initial = df["close"].iloc[0]
        final = df["close"].iloc[-1]
        total_return = (final - initial) / initial * 100

        return BacktestResult(
            name="buy_and_hold",
            start_date=str(df.iloc[0].get("timestamp", "")),
            end_date=str(df.iloc[-1].get("timestamp", "")),
            total_trades=1,
            win_rate=1.0 if total_return > 0 else 0.0,
            total_return_pct=total_return,
            annual_return_pct=self._annualize(initial, final,
                                              start_date=df.iloc[0].get("timestamp"),
                                              end_date=df.iloc[-1].get("timestamp")),
            max_drawdown_pct=self._calc_max_dd(df["close"].values),
            trades=[{"date": str(df.iloc[0].get("timestamp", "")), "action": "buy",
                      "price": initial, "pnl": 0}],
        )

    # ------------------------------------------------------------------
    # 批量回测 + 信号验证
    # ------------------------------------------------------------------

    def validate_signals(
        self, gold_df: pd.DataFrame, lookahead_days: int = 5,
    ) -> dict[str, float]:
        """验证各信号对未来N天的预测准确率."""
        df = gold_df.reset_index(drop=True)
        closes = df["close"].values
        results: dict[str, float] = {}

        # RSI信号
        if len(df) > 30:
            correct_rsi = 0
            total_rsi = 0
            delta = df["close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, 1)
            rsi_values = 100 - (100 / (1 + rs))

            for i in range(14, len(df) - lookahead_days):
                if rsi_values.iloc[i] < 30:
                    total_rsi += 1
                    if closes[i + lookahead_days] > closes[i]:
                        correct_rsi += 1
            results["RSI_oversold_buy"] = correct_rsi / total_rsi if total_rsi else 0

        # 均线信号
        if len(df) > 30:
            ma5 = df["close"].rolling(5).mean()
            ma20 = df["close"].rolling(20).mean()
            correct_ma = 0
            total_ma = 0
            for i in range(20, len(df) - lookahead_days):
                if ma5.iloc[i] > ma20.iloc[i] and ma5.iloc[i - 1] <= ma20.iloc[i - 1]:
                    total_ma += 1
                    if closes[i + lookahead_days] > closes[i]:
                        correct_ma += 1
            results["MA_crossover_buy"] = correct_ma / total_ma if total_ma else 0

        # ETF资金流信号 (如果有)
        # 这里只做技术信号验证，资金流需要额外数据

        logger.info(f"信号验证结果 ({lookahead_days}日后): {results}")
        return results

    # ------------------------------------------------------------------
    # 保存/加载
    # ------------------------------------------------------------------

    def save(self, result: BacktestResult) -> str:
        filename = f"{result.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.data_dir / filename
        with open(filepath, "w") as f:
            json.dump({
                "name": result.name,
                "start_date": result.start_date,
                "end_date": result.end_date,
                "total_trades": result.total_trades,
                "win_rate": result.win_rate,
                "total_return_pct": result.total_return_pct,
                "annual_return_pct": result.annual_return_pct,
                "max_drawdown_pct": result.max_drawdown_pct,
                "sharpe_ratio": result.sharpe_ratio,
                "profit_factor": result.profit_factor,
                "trades": result.trades[-10:],  # 只保留最近10笔
            }, f, indent=2, ensure_ascii=False, default=str)
        return str(filepath)

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_metrics(
        name: str, start_date: str, end_date: str,
        trades: list[dict], daily_values: list[float],
        initial_capital: float,
    ) -> BacktestResult:
        """从交易记录计算回测指标."""
        n = len(trades)
        if n == 0:
            return BacktestResult(name=name, start_date=start_date, end_date=end_date)

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        win_rate = len(wins) / n if n else 0
        total_return = (daily_values[-1] - initial_capital) / initial_capital * 100 if daily_values else 0
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = np.mean([t["pnl"] for t in losses]) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 0

        # 最大回撤
        values = np.array(daily_values) if daily_values else np.array([])
        max_dd = BacktestEngine._calc_max_dd(values)

        # 年化收益
        ann_ret = BacktestEngine._annualize(
            initial_capital, initial_capital + total_return * initial_capital / 100,
            start_date, end_date,
        )

        # 夏普比率
        sharpe = BacktestEngine._calc_sharpe(values) if len(values) > 1 else 0

        return BacktestResult(
            name=name, start_date=start_date, end_date=end_date,
            total_trades=n, win_rate=win_rate,
            total_return_pct=total_return,
            annual_return_pct=ann_ret,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            profit_factor=profit_factor,
            trades=trades,
        )

    @staticmethod
    def _calc_max_dd(values: np.ndarray) -> float:
        if len(values) < 2:
            return 0.0
        peak = np.maximum.accumulate(values)
        dd = (peak - values) / peak * 100
        return float(np.max(dd))

    @staticmethod
    def _calc_sharpe(daily_values: np.ndarray, risk_free: float = 0.02) -> float:
        if len(daily_values) < 2:
            return 0.0
        returns = np.diff(daily_values) / daily_values[:-1]
        excess = returns - risk_free / 252
        if np.std(returns) == 0:
            return 0.0
        return float(np.mean(excess) / np.std(returns) * np.sqrt(252))

    @staticmethod
    def _annualize(
        start_val: float, end_val: float,
        start_date: Any, end_date: Any,
    ) -> float:
        try:
            if isinstance(start_date, str) and isinstance(end_date, str):
                d1 = pd.Timestamp(start_date)
                d2 = pd.Timestamp(end_date)
                days = (d2 - d1).days
            else:
                days = 365
            if days < 1:
                days = 1
            total_ret = (end_val - start_val) / start_val
            return ((1 + total_ret) ** (365 / days) - 1) * 100
        except Exception:
            return 0.0
