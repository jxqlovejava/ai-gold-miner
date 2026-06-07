"""多目标策略体系 — 盈利最大化 / 回本优先 / 落袋为安."""

from gold_miner.strategy.engine import MultiObjectiveEngine, StrategyComparison
from gold_miner.strategy.objectives import (
    BalancedStrategy,
    CostRecoveryStrategy,
    MaxProfitStrategy,
    StrategyConfig,
    StrategyDecision,
    StrategyObjective,
    TakeProfitStrategy,
)
from gold_miner.strategy.safety import SafetyMargin, SafetyMarginCalculator

__all__ = [
    "BalancedStrategy",
    "CostRecoveryStrategy",
    "MaxProfitStrategy",
    "MultiObjectiveEngine",
    "SafetyMargin",
    "SafetyMarginCalculator",
    "StrategyComparison",
    "StrategyConfig",
    "StrategyDecision",
    "StrategyObjective",
    "TakeProfitStrategy",
]
