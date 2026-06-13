"""ATR 移动止盈模块.

实现基于真实波幅(ATR)的移动止损/止盈策略:
- 多头: 止损价 = 持仓期间最高价 - multiplier × ATR
- 价格创新高, 止损价跟随上移
- 价格从高点回撤 multiplier×ATR, 触发减仓/止盈
"""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class TrailingStopSignal:
    """ATR 移动止盈信号."""

    timestamp: datetime
    current_price: float
    highest_high: float
    atr: float
    multiplier: float
    stop_price: float
    triggered: bool
    action: str  # "hold" | "reduce_half" | "close_all"
    reason: str


class ATRTrailingStop:
    """ATR 移动止盈计算器.

    Args:
        atr_period: ATR 计算周期, 默认 14
        multiplier: ATR 乘数, 默认 2.5
        hard_stop_price: 硬止损价, 不可突破
        reduce_action: 触发移动止盈后的动作, 默认 "reduce_half"
    """

    def __init__(
        self,
        atr_period: int = 14,
        multiplier: float = 2.5,
        hard_stop_price: float | None = None,
        reduce_action: str = "reduce_half",
    ) -> None:
        if atr_period <= 0:
            raise ValueError("atr_period 必须大于 0")
        if multiplier <= 0:
            raise ValueError("multiplier 必须大于 0")

        self.atr_period = atr_period
        self.multiplier = multiplier
        self.hard_stop_price = hard_stop_price
        self.reduce_action = reduce_action

    def calculate(
        self,
        df: pd.DataFrame,
        entry_price: float | None = None,
    ) -> TrailingStopSignal:
        """计算最新 ATR 移动止盈信号.

        Args:
            df: 包含 open/high/low/close 列的 DataFrame
            entry_price: 进场价格, 用于初始化最高价; 为 None 时使用历史最低价

        Returns:
            TrailingStopSignal
        """
        required = {"open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame 缺少列: {missing}")

        if len(df) < self.atr_period:
            raise ValueError(
                f"数据不足: 需要至少 {self.atr_period} 条, 当前 {len(df)}"
            )

        df = df.copy()
        df["tr"] = self._true_range(df)
        df["atr"] = df["tr"].rolling(window=self.atr_period).mean()

        latest = df.iloc[-1]
        current_price = float(latest["close"])
        latest_atr = float(latest["atr"])

        # 持仓期间最高价: 进场价与历史高点的较大值
        historical_high = float(df["high"].max())
        if entry_price is not None:
            highest_high = max(entry_price, historical_high)
        else:
            highest_high = historical_high

        # ATR 移动止盈价
        trailing_stop = highest_high - self.multiplier * latest_atr

        # 硬止损约束
        if self.hard_stop_price is not None:
            effective_stop = max(trailing_stop, self.hard_stop_price)
        else:
            effective_stop = trailing_stop

        # 判断触发
        if current_price <= effective_stop:
            triggered = True
            action = self.reduce_action
            reason = (
                f"价格 {current_price:.2f} 触及移动止盈位 {effective_stop:.2f} "
                f"(从高点 {highest_high:.2f} 回撤 {self.multiplier}×ATR={self.multiplier * latest_atr:.2f})"
            )
        else:
            triggered = False
            action = "hold"
            reason = (
                f"未触发: 当前 {current_price:.2f}, 移动止盈位 {effective_stop:.2f}, "
                f"距离 {(current_price - effective_stop):.2f}"
            )

        # 提取最新数据的时间戳
        if "timestamp" in latest.index:
            ts = pd.to_datetime(latest["timestamp"])
        elif isinstance(latest.name, (str, datetime)):
            ts = pd.to_datetime(latest.name)
        else:
            ts = datetime.now()

        return TrailingStopSignal(
            timestamp=ts,
            current_price=round(current_price, 2),
            highest_high=round(highest_high, 2),
            atr=round(latest_atr, 2),
            multiplier=self.multiplier,
            stop_price=round(effective_stop, 2),
            triggered=triggered,
            action=action,
            reason=reason,
        )

    def _true_range(self, df: pd.DataFrame) -> pd.Series:
        """计算真实波幅 True Range."""
        high_low = df["high"] - df["low"]
        high_close_prev = (df["high"] - df["close"].shift(1)).abs()
        low_close_prev = (df["low"] - df["close"].shift(1)).abs()
        return pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(
            axis=1
        )


def format_signal(signal: TrailingStopSignal) -> str:
    """格式化信号为可读文本."""
    lines = [
        f"日期: {signal.timestamp}",
        f"当前价: {signal.current_price}",
        f"持仓期间最高价: {signal.highest_high}",
        f"14日 ATR: {signal.atr}",
        f"ATR 乘数: {signal.multiplier}",
        f"移动止盈价: {signal.stop_price}",
        f"触发状态: {'已触发' if signal.triggered else '未触发'}",
        f"建议动作: {signal.action}",
        f"说明: {signal.reason}",
    ]
    return "\n".join(lines)
