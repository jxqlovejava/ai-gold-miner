"""策略目标与配置 — 四种策略目标的不同参数."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrategyObjective(str, Enum):
    MAXIMIZE_PROFIT = "maximize_profit"
    COST_RECOVERY = "cost_recovery"
    TAKE_PROFIT = "take_profit"
    BALANCED = "balanced"


@dataclass
class StrategyConfig:
    objective: StrategyObjective
    position_sizing_method: str = "base"  # base | kelly | halved | progressive
    stop_loss_method: str = "fixed"  # fixed | atr | trailing
    take_profit_method: str = "fixed"  # fixed | multi_level | trailing
    max_position_pct: float = 0.8
    min_safety_margin: float = 0.02
    stop_loss_atr_mult: float = 1.0
    take_profit_atr_mult: float = 2.0
    take_profit_levels: list[float] = field(default_factory=list)
    tp_level_weights: list[float] = field(default_factory=list)  # 各档止盈比例分配
    activation_trigger: str = ""  # always | drawdown | profit_high
    activation_threshold: float = 0.0  # 触发阈值 (正=收益%, 负=亏损%)
    description: str = ""

    @classmethod
    def for_objective(cls, objective: StrategyObjective) -> "StrategyConfig":
        """工厂方法 — 为每种目标提供合理默认值."""
        if objective == StrategyObjective.MAXIMIZE_PROFIT:
            return cls(
                objective=objective,
                position_sizing_method="kelly",
                stop_loss_method="atr",
                take_profit_method="multi_level",
                max_position_pct=0.8,
                stop_loss_atr_mult=2.0,
                take_profit_levels=[0.03, 0.06, 0.10],
                tp_level_weights=[0.4, 0.3, 0.3],
                activation_trigger="always",
                description="盈利最大化: 凯利公式仓位, 宽止损, 三档分批止盈 3%/6%/10%",
            )

        if objective == StrategyObjective.COST_RECOVERY:
            return cls(
                objective=objective,
                position_sizing_method="halved",
                stop_loss_method="atr",
                take_profit_method="fixed",
                max_position_pct=0.4,
                stop_loss_atr_mult=1.0,
                take_profit_levels=[0.02],
                tp_level_weights=[1.0],
                activation_trigger="drawdown",
                activation_threshold=-0.05,
                description="回本优先: 半仓操作, 紧密止损, 回本即止盈",
            )

        if objective == StrategyObjective.TAKE_PROFIT:
            return cls(
                objective=objective,
                position_sizing_method="progressive",
                stop_loss_method="trailing",
                take_profit_method="multi_level",
                max_position_pct=0.6,
                stop_loss_atr_mult=1.5,
                take_profit_levels=[0.02, 0.05, 0.08],
                tp_level_weights=[0.3, 0.3, 0.4],
                activation_trigger="profit_high",
                activation_threshold=0.08,
                description="落袋为安: 仓位递减, 移动止损, 分批锁定利润",
            )

        # BALANCED — 兜底
        return cls(
            objective=objective,
            position_sizing_method="base",
            stop_loss_method="atr",
            take_profit_method="fixed",
            max_position_pct=0.6,
            stop_loss_atr_mult=1.0,
            take_profit_atr_mult=2.0,
            activation_trigger="always",
            description="均衡策略: 标准仓位, 标准止损, 常规止盈",
        )


@dataclass
class StrategyDecision:
    objective: StrategyObjective
    direction: str  # long | short | neutral
    position_pct: float
    stop_loss: float
    take_profit_levels: list[float] = field(default_factory=list)
    tp_weights: list[float] = field(default_factory=list)
    confidence: float = 0.5
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------


class BaseStrategy:
    """策略基类."""

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def should_activate(self, portfolio_return: float) -> bool:
        trigger = self.config.activation_trigger
        threshold = self.config.activation_threshold

        if trigger == "always":
            return True
        if trigger == "drawdown":
            return portfolio_return < threshold
        if trigger == "profit_high":
            return portfolio_return > threshold
        return True

    def compute_position_size(
        self, base_pct: float, volatility: float, profit_pct: float = 0.0,
    ) -> float:
        """计算仓位."""
        method = self.config.position_sizing_method
        max_pos = self.config.max_position_pct

        if method == "halved":
            pct = base_pct * 0.5
        elif method == "progressive":
            # 盈利越多，仓位越小 (落袋为安)
            reduction = min(profit_pct / 0.15, 0.5)
            pct = base_pct * (1.0 - reduction)
        elif method == "kelly":
            # 简化凯利公式: f* = (p*b - q) / b
            # p = win_rate 估算, b = reward/risk 比
            win_rate = 0.55  # 默认胜率估算
            reward_risk = self.config.take_profit_atr_mult / self.config.stop_loss_atr_mult
            k = (win_rate * reward_risk - (1 - win_rate)) / reward_risk
            pct = base_pct * max(0.1, min(k, 1.0))
        else:
            pct = base_pct

        # 波动率调整
        if volatility > 0.02:
            pct *= max(0.5, 1.0 - (volatility - 0.02) * 10)

        return round(min(pct, max_pos), 2)

    def compute_stop_loss(self, entry_price: float, atr: float) -> float:
        """计算止损位."""
        method = self.config.stop_loss_method
        mult = self.config.stop_loss_atr_mult

        if method == "trailing":
            # 移动止损: 从最高点回撤 1.5× ATR
            return round(entry_price * (1 - mult * atr / entry_price), 2)
        if method == "fixed":
            return round(entry_price * (1 - 0.03), 2)
        # atr
        return round(entry_price - mult * atr, 2)

    def compute_take_profit(self, entry_price: float, atr: float) -> list[tuple[float, float]]:
        """计算止盈位列表. 返回 [(价格, 比例), ...]."""
        method = self.config.take_profit_method
        levels = self.config.take_profit_levels
        weights = self.config.tp_level_weights

        if method == "fixed":
            return [(round(entry_price * (1 + self.config.take_profit_atr_mult * atr / entry_price), 2), 1.0)]

        if method == "multi_level" and levels:
            return [
                (round(entry_price * (1 + level), 2), weights[i] if i < len(weights) else 1.0 / len(levels))
                for i, level in enumerate(levels)
            ]

        return [(round(entry_price * 1.06), 1.0)]

    def decide(
        self,
        direction: str,
        base_position: float,
        entry_price: float,
        atr: float,
        portfolio_return: float = 0.0,
        volatility: float = 0.01,
    ) -> StrategyDecision:
        """生成策略决策."""
        if not self.should_activate(portfolio_return):
            return StrategyDecision(
                objective=self.config.objective,
                direction="neutral",
                position_pct=0.0,
                stop_loss=0.0,
                reason=f"{self.config.objective.value}: 未激活",
            )

        pos = self.compute_position_size(base_position, volatility, portfolio_return)
        stop = self.compute_stop_loss(entry_price, atr)
        tp_levels = self.compute_take_profit(entry_price, atr)

        return StrategyDecision(
            objective=self.config.objective,
            direction=direction if pos > 0 else "neutral",
            position_pct=pos,
            stop_loss=stop,
            take_profit_levels=[t[0] for t in tp_levels],
            tp_weights=[t[1] for t in tp_levels],
            confidence=round(min(pos / self.config.max_position_pct, 1.0), 2),
            reason=f"{self.config.objective.value}: {self.config.description}",
            metadata={
                "position_method": self.config.position_sizing_method,
                "stop_method": self.config.stop_loss_method,
                "tp_method": self.config.take_profit_method,
                "atr": round(atr, 2),
                "volatility": round(volatility, 4),
            },
        )


# ---------------------------------------------------------------------------
# 具体策略
# ---------------------------------------------------------------------------


class MaxProfitStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(StrategyConfig.for_objective(StrategyObjective.MAXIMIZE_PROFIT))


class CostRecoveryStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(StrategyConfig.for_objective(StrategyObjective.COST_RECOVERY))


class TakeProfitStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(StrategyConfig.for_objective(StrategyObjective.TAKE_PROFIT))


class BalancedStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__(StrategyConfig.for_objective(StrategyObjective.BALANCED))
