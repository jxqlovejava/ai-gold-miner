"""ATR 双轨移动止损/止盈模块.

实现基于真实波幅(ATR)的双轨策略:
- 浮盈轨: 止损价 = max(持仓期间最高价 - profit_multiplier × ATR, 成本价)
- 浮亏轨: 止损价 = max(成本价 - loss_multiplier × ATR, 硬止损价)
- 价格创新高, 浮盈轨跟随上移
- 价格持续下跌, 浮亏轨限制亏损扩大
"""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class TrailingStopSignal:
    """ATR 双轨移动止损/止盈信号."""

    timestamp: datetime
    current_price: float
    cost_basis: float | None
    highest_high: float
    atr: float
    profit_multiplier: float
    loss_multiplier: float
    stop_price: float
    track: str  # "profit" | "loss" | "hard_stop" | "none"
    triggered: bool
    action: str  # "hold" | "reduce_half" | "close_all"
    reason: str


class ATRTrailingStop:
    """ATR 双轨移动止损/止盈计算器.

    Args:
        atr_period: ATR 计算周期, 默认 14
        profit_multiplier: 浮盈轨 ATR 乘数, 默认 2.5
        loss_multiplier: 浮亏轨 ATR 乘数, 默认 3.0
        cost_basis: 成本价, 浮盈轨不低于成本价
        hard_stop_price: 硬止损价, 不可突破
        profit_action: 浮盈轨触发动作, 默认 "reduce_half"
        loss_action: 浮亏轨触发动作, 默认 "reduce_half"
    """

    def __init__(
        self,
        atr_period: int = 14,
        profit_multiplier: float = 2.5,
        loss_multiplier: float = 3.0,
        cost_basis: float | None = None,
        hard_stop_price: float | None = None,
        profit_action: str = "reduce_half",
        loss_action: str = "reduce_half",
    ) -> None:
        if atr_period <= 0:
            raise ValueError("atr_period 必须大于 0")
        if profit_multiplier <= 0:
            raise ValueError("profit_multiplier 必须大于 0")
        if loss_multiplier <= 0:
            raise ValueError("loss_multiplier 必须大于 0")

        self.atr_period = atr_period
        self.profit_multiplier = profit_multiplier
        self.loss_multiplier = loss_multiplier
        self.cost_basis = cost_basis
        self.hard_stop_price = hard_stop_price
        self.profit_action = profit_action
        self.loss_action = loss_action

    def calculate(
        self,
        df: pd.DataFrame,
        entry_price: float | None = None,
    ) -> TrailingStopSignal:
        """计算最新 ATR 双轨移动止损/止盈信号.

        Args:
            df: 包含 open/high/low/close 列的 DataFrame
            entry_price: 进场价格, 用于初始化最高价; 为 None 时使用历史最高价

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

        cost_basis = self.cost_basis
        in_profit = cost_basis is not None and current_price > cost_basis

        # 计算双轨止损价
        if in_profit:
            # 浮盈轨: 从最高点回撤 profit_multiplier×ATR, 但不低于成本价
            profit_stop = highest_high - self.profit_multiplier * latest_atr
            trailing_stop = profit_stop if cost_basis is None else max(profit_stop, cost_basis)
            track = "profit"
            action = self.profit_action
        else:
            # 浮亏轨: 从成本价下跌 loss_multiplier×ATR
            if cost_basis is not None:
                loss_stop = cost_basis - self.loss_multiplier * latest_atr
                trailing_stop = loss_stop
                track = "loss"
                action = self.loss_action
            else:
                # 无成本价时, 使用与浮盈轨相同的逻辑
                trailing_stop = highest_high - self.profit_multiplier * latest_atr
                track = "profit"
                action = self.profit_action

        # 硬止损约束
        if self.hard_stop_price is not None:
            effective_stop = max(trailing_stop, self.hard_stop_price)
        else:
            effective_stop = trailing_stop

        # 判断触发
        if current_price <= effective_stop:
            triggered = True
            # 若价格直接跌破硬止损, 报告为 hard_stop 并无条件清仓
            if (
                self.hard_stop_price is not None
                and current_price <= self.hard_stop_price
            ):
                track = "hard_stop"
                action = "close_all"
            reason = self._build_trigger_reason(
                current_price,
                effective_stop,
                highest_high,
                latest_atr,
                track,
                in_profit,
                cost_basis,
            )
        else:
            triggered = False
            action = "hold"
            reason = (
                f"未触发: 当前 {current_price:.2f}, 止损位 {effective_stop:.2f}, "
                f"距离 {(current_price - effective_stop):.2f}"
            )
            if cost_basis is not None:
                if in_profit:
                    reason += (
                        f"；当前浮盈, 浮盈轨(最高-{self.profit_multiplier}×ATR)生效中"
                    )
                else:
                    reason += (
                        f"；当前浮亏, 浮亏轨(成本-{self.loss_multiplier}×ATR)生效中, "
                        f"硬止损 {self.hard_stop_price} 为最后防线"
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
            cost_basis=cost_basis,
            highest_high=round(highest_high, 2),
            atr=round(latest_atr, 2),
            profit_multiplier=self.profit_multiplier,
            loss_multiplier=self.loss_multiplier,
            stop_price=round(effective_stop, 2),
            track=track,
            triggered=triggered,
            action=action,
            reason=reason,
        )

    def _build_trigger_reason(
        self,
        current_price: float,
        effective_stop: float,
        highest_high: float,
        latest_atr: float,
        track: str,
        in_profit: bool,
        cost_basis: float | None,
    ) -> str:
        """构建触发说明."""
        if track == "hard_stop":
            return (
                f"价格 {current_price:.2f} 触及硬止损位 {effective_stop:.2f}, "
                f"无条件离场"
            )

        if in_profit and cost_basis is not None:
            return (
                f"价格 {current_price:.2f} 触及浮盈止损位 {effective_stop:.2f} "
                f"(从高点 {highest_high:.2f} 回撤 {self.profit_multiplier}×ATR="
                f"{self.profit_multiplier * latest_atr:.2f}, 已保本 {cost_basis:.2f})"
            )

        if cost_basis is not None:
            return (
                f"价格 {current_price:.2f} 触及浮亏止损位 {effective_stop:.2f} "
                f"(成本价 {cost_basis:.2f} 下跌 {self.loss_multiplier}×ATR="
                f"{self.loss_multiplier * latest_atr:.2f})"
            )

        return (
            f"价格 {current_price:.2f} 触及止损位 {effective_stop:.2f} "
            f"(从高点 {highest_high:.2f} 回撤 {self.profit_multiplier}×ATR="
            f"{self.profit_multiplier * latest_atr:.2f})"
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
        f"成本价: {signal.cost_basis if signal.cost_basis else '未设置'}",
        f"持仓期间最高价: {signal.highest_high}",
        f"14日 ATR: {signal.atr}",
        f"浮盈轨: 最高 - {signal.profit_multiplier}×ATR",
        f"浮亏轨: 成本 - {signal.loss_multiplier}×ATR",
        f"有效止损位: {signal.stop_price}",
        f"当前轨道: {signal.track}",
        f"触发状态: {'已触发' if signal.triggered else '未触发'}",
        f"建议动作: {signal.action}",
        f"说明: {signal.reason}",
    ]
    return "\n".join(lines)
