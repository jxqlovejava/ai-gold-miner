"""中长期分析结果数据类."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gold_miner.decision.agents import AgentOpinion
from gold_miner.doctrine.checker import DoctrineResult
from gold_miner.signals.base import SignalBundle
from gold_miner.signals.long_term_scenario import ScenarioMatrix


@dataclass
class LongTermAnalysisResult:
    """中长期金价分析结果."""

    success: bool = True
    horizon_months: int = 12
    current_spot: float = 0.0
    bundle: SignalBundle = field(default_factory=SignalBundle)
    bull_opinion: AgentOpinion | None = None
    bear_opinion: AgentOpinion | None = None
    trade_decision: dict[str, Any] = field(default_factory=dict)
    doctrine_result: DoctrineResult | None = None
    scenario_matrix: ScenarioMatrix | None = None
    investor_profile: str = ""
    portfolio: dict[str, Any] = field(default_factory=dict)
    munger_models: list[str] = field(default_factory=list)
    strategic_recommendation: dict[str, Any] = field(default_factory=dict)
    trigger_conditions: list[str] = field(default_factory=list)
    rebalancing_rules: list[str] = field(default_factory=list)
    low_buy_high_sell: dict[str, Any] = field(default_factory=dict)  # V9 分级低吸高抛建议
    # 情景预案结构化触发条件 (关键价+时间窗+证伪点+动作)
    scenario_triggers: list[Any] = field(default_factory=list)
    # 由情景触发条件推导的条件单建议
    conditional_order_suggestions: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)

    def to_report_dict(self) -> dict[str, Any]:
        """转为报告字典."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "horizon_months": self.horizon_months,
            "current_spot": self.current_spot,
            "summary": self.strategic_recommendation,
            "signals": [
                {
                    "name": s.name,
                    "dimension": s.dimension,
                    "direction": s.direction.value,
                    "strength": s.strength.value,
                    "score": s.score,
                    "description": s.description,
                }
                for s in self.bundle.signals
            ],
            "bull_opinion": self._opinion_dict(self.bull_opinion),
            "bear_opinion": self._opinion_dict(self.bear_opinion),
            "trade_decision": self.trade_decision,
            "doctrine_result": self._doctrine_dict(self.doctrine_result),
            "scenario_matrix": self._scenario_dict(self.scenario_matrix),
            "munger_models": self.munger_models,
            "trigger_conditions": self.trigger_conditions,
            "rebalancing_rules": self.rebalancing_rules,
            "low_buy_high_sell": self.low_buy_high_sell,
            "scenario_triggers": [
                t.to_dict() if hasattr(t, "to_dict") else t
                for t in self.scenario_triggers
            ],
            "conditional_order_suggestions": self.conditional_order_suggestions,
            "messages": self.messages,
            "warnings": self.warnings,
        }

    @staticmethod
    def _opinion_dict(opinion: AgentOpinion | None) -> dict[str, Any]:
        if opinion is None:
            return {}
        return {
            "agent_name": opinion.agent_name,
            "stance": opinion.stance,
            "confidence": opinion.confidence,
            "suggested_position_pct": opinion.suggested_position_pct,
            "arguments": opinion.arguments,
        }

    @staticmethod
    def _doctrine_dict(result: DoctrineResult | None) -> dict[str, Any]:
        if result is None:
            return {}
        return {
            "passed_count": result.passed_count,
            "failed_count": result.failed_count,
            "has_blocks": result.has_blocks,
            "blocks": [v.rule.id for v in result.blocks],
            "warnings": [v.rule.id for v in result.warnings],
        }

    @staticmethod
    def _scenario_dict(matrix: ScenarioMatrix | None) -> dict[str, Any]:
        if matrix is None:
            return {}
        return {
            "base_price": matrix.base_price,
            "horizon_months": matrix.horizon_months,
            "expected_price": matrix.expected_price,
            "weighted_expected_change_pct": matrix.weighted_expected_change_pct,
            "scenarios": [
                {
                    "name": s.name,
                    "probability_pct": s.probability_pct,
                    "gold_change_pct": s.gold_change_pct,
                    "gold_low": s.gold_low,
                    "gold_high": s.gold_high,
                    "reasoning": s.reasoning,
                }
                for s in matrix.scenarios
            ],
        }
