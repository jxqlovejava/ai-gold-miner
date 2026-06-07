"""多目标策略引擎 — 评估+推荐."""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from gold_miner.signals.base import SignalBundle
from gold_miner.strategy.objectives import (
    BalancedStrategy,
    BaseStrategy,
    CostRecoveryStrategy,
    MaxProfitStrategy,
    StrategyDecision,
    StrategyObjective,
    TakeProfitStrategy,
)
from gold_miner.strategy.safety import SafetyMarginCalculator


@dataclass
class StrategyComparison:
    results: dict[str, StrategyDecision] = field(default_factory=dict)
    recommended: StrategyObjective | None = None
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


class MultiObjectiveEngine:
    """多目标策略引擎.

    优先级: CostRecovery > TakeProfit > MaxProfit > Balanced
    """

    STRATEGY_PRIORITY = [
        StrategyObjective.COST_RECOVERY,
        StrategyObjective.TAKE_PROFIT,
        StrategyObjective.MAXIMIZE_PROFIT,
        StrategyObjective.BALANCED,
    ]

    def __init__(self) -> None:
        self._strategies: dict[StrategyObjective, BaseStrategy] = {
            StrategyObjective.MAXIMIZE_PROFIT: MaxProfitStrategy(),
            StrategyObjective.COST_RECOVERY: CostRecoveryStrategy(),
            StrategyObjective.TAKE_PROFIT: TakeProfitStrategy(),
            StrategyObjective.BALANCED: BalancedStrategy(),
        }
        self.safety = SafetyMarginCalculator()

    def evaluate(
        self,
        bundle: SignalBundle,
        portfolio_return: float = 0.0,
        current_position_pct: float = 0.0,
        entry_price: float = 0.0,
        atr: float = 0.0,
        volatility: float = 0.01,
        dxy_correlation: float = 0.0,
    ) -> StrategyComparison:
        """评估所有适用策略."""
        comparison = StrategyComparison()

        direction = "long" if bundle.composite_score > 0 else "short" if bundle.composite_score < 0 else "neutral"
        base_position = abs(bundle.composite_score)

        # 安全边际计算
        margins = self.safety.calculate(
            direction=direction,
            entry_price=entry_price,
            volatility=volatility,
            dxy_correlation=dxy_correlation,
        )
        safety_penalty = sum(1 for m in margins if not m.passed) * 0.1

        for objective in self.STRATEGY_PRIORITY:
            strategy = self._strategies[objective]

            if not strategy.should_activate(portfolio_return):
                continue

            decision = strategy.decide(
                direction=direction,
                base_position=base_position,
                entry_price=entry_price,
                atr=atr,
                portfolio_return=portfolio_return,
                volatility=volatility,
            )

            # 安全边际惩罚
            if safety_penalty > 0 and decision.position_pct > 0:
                decision.position_pct = round(
                    max(0.0, decision.position_pct * (1.0 - safety_penalty)), 2
                )
                decision.reason += f" (安全边际扣减 {safety_penalty:.0%})"

            comparison.results[objective.value] = decision

        # 推荐逻辑: 找到第一个激活且仓位>0的策略
        recommended = None
        for obj in self.STRATEGY_PRIORITY:
            dec = comparison.results.get(obj.value)
            if dec and dec.position_pct > 0:
                recommended = obj
                break

        if recommended is None:
            # 全部未激活 → 均衡兜底
            bal = self._strategies[StrategyObjective.BALANCED].decide(
                direction=direction,
                base_position=base_position,
                entry_price=entry_price,
                atr=atr,
            )
            comparison.results[StrategyObjective.BALANCED.value] = bal
            recommended = StrategyObjective.BALANCED

        comparison.recommended = recommended
        comparison.reasoning = self._build_reasoning(comparison, portfolio_return)
        logger.info(
            f"策略引擎: 推荐 {recommended.value}, "
            f"组合收益 {portfolio_return:+.1%}, "
            f"方向 {direction}"
        )
        return comparison

    def _build_reasoning(
        self, comparison: StrategyComparison, portfolio_return: float,
    ) -> str:
        parts: list[str] = []
        rec = comparison.recommended
        if rec:
            dec = comparison.results.get(rec.value)
            if dec:
                parts.append(f"推荐 {rec.value}: {dec.reason}")
        parts.append(f"组合收益 {portfolio_return:+.2%}")
        return " | ".join(parts)

    def resolve_conflicts(
        self, comparison: StrategyComparison,
    ) -> StrategyDecision:
        """解决多策略冲突，返回最终决策."""
        decisions = list(comparison.results.values())
        active = [d for d in decisions if d.position_pct > 0]

        if not active:
            return StrategyDecision(
                objective=StrategyObjective.BALANCED,
                direction="neutral",
                position_pct=0.0,
                stop_loss=0.0,
                reason="无活跃策略",
            )

        # 推荐策略优先
        rec = comparison.recommended
        if rec and rec.value in comparison.results:
            return comparison.results[rec.value]

        # 取中间仓位
        sorted_by_pos = sorted(active, key=lambda d: d.position_pct)
        return sorted_by_pos[len(sorted_by_pos) // 2]
