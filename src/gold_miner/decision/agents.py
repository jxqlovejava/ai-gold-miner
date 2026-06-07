"""多Agent辩论系统 — 多头 vs 空头 vs 风控."""

from dataclasses import dataclass, field
from typing import Any

from gold_miner.signals.base import SignalBundle


@dataclass
class AgentOpinion:
    agent_name: str
    stance: str
    confidence: float
    arguments: list[str] = field(default_factory=list)
    suggested_position_pct: float = 0.0


class BullAgent:
    NAME = "多头分析师"

    def analyze(self, bundle: SignalBundle) -> AgentOpinion:
        bullish_signals = [s for s in bundle.signals if s.score > 0]
        bearish_signals = [s for s in bundle.signals if s.score < 0]

        arguments: list[str] = []
        for s in sorted(bullish_signals, key=lambda x: abs(x.score), reverse=True)[:3]:
            arguments.append(f"[{s.name}] {s.description} (评分: {s.score:+.2f})")

        bull_score = sum(s.score for s in bullish_signals)
        bear_score = sum(abs(s.score) for s in bearish_signals)
        total = bull_score + bear_score

        confidence = bull_score / total if total > 0 else 0.5
        stance = "bullish" if confidence > 0.55 else "neutral"
        suggested = min(confidence * 0.8, 0.8)

        return AgentOpinion(
            agent_name=self.NAME,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            suggested_position_pct=suggested,
        )


class BearAgent:
    NAME = "空头分析师"

    def analyze(self, bundle: SignalBundle) -> AgentOpinion:
        bearish_signals = [s for s in bundle.signals if s.score < 0]
        bullish_signals = [s for s in bundle.signals if s.score > 0]

        arguments: list[str] = []
        for s in sorted(bearish_signals, key=lambda x: abs(x.score), reverse=True)[:3]:
            arguments.append(f"[{s.name}] {s.description} (评分: {s.score:+.2f})")

        bear_score = sum(abs(s.score) for s in bearish_signals)
        bull_score = sum(s.score for s in bullish_signals)
        total = bear_score + bull_score

        confidence = bear_score / total if total > 0 else 0.5
        stance = "bearish" if confidence > 0.55 else "neutral"
        suggested = min(confidence * 0.8, 0.8)

        return AgentOpinion(
            agent_name=self.NAME,
            stance=stance,
            confidence=confidence,
            arguments=arguments,
            suggested_position_pct=suggested,
        )


class PortfolioManager:
    NAME = "投资经理"

    def decide(
        self,
        bull: AgentOpinion,
        bear: AgentOpinion,
        bundle: SignalBundle,
        risk_profile: str = "moderate",
        strategy_decision: Any | None = None,
    ) -> dict[str, Any]:
        risk_multipliers = {"aggressive": 1.2, "moderate": 1.0, "conservative": 0.6}
        multiplier = risk_multipliers.get(risk_profile, 1.0)

        if bull.stance == "bullish" and bear.stance != "bearish":
            direction = "long"
            raw_position = bull.suggested_position_pct
        elif bear.stance == "bearish" and bull.stance != "bullish":
            direction = "short"
            raw_position = bear.suggested_position_pct
        else:
            net_score = bundle.composite_score
            if net_score > 0.2:
                direction = "long"
                raw_position = abs(net_score)
            elif net_score < -0.2:
                direction = "short"
                raw_position = abs(net_score)
            else:
                direction = "neutral"
                raw_position = 0.0

        position_pct = min(raw_position * multiplier, 0.9)

        if position_pct > 0.5:
            signal_type = "强信号"
        elif position_pct > 0.2:
            signal_type = "中等信号"
        elif position_pct > 0:
            signal_type = "弱信号"
        else:
            signal_type = "无信号"

        result = {
            "direction": direction,
            "position_pct": round(position_pct, 2),
            "signal_type": signal_type,
            "risk_profile": risk_profile,
            "bull_confidence": round(bull.confidence, 2),
            "bear_confidence": round(bear.confidence, 2),
            "composite_score": round(bundle.composite_score, 2),
            "debate_summary": {
                "bull_args": bull.arguments,
                "bear_args": bear.arguments,
            },
        }

        # 策略目标覆盖
        if strategy_decision is not None and strategy_decision.position_pct > 0:
            result["direction"] = strategy_decision.direction
            result["position_pct"] = min(
                strategy_decision.position_pct, position_pct * 1.1
            )
            result["strategy_objective"] = strategy_decision.objective.value
            result["strategy_reason"] = strategy_decision.reason
            result["stop_loss"] = strategy_decision.stop_loss
            result["take_profit_levels"] = strategy_decision.take_profit_levels
            result["tp_weights"] = strategy_decision.tp_weights

        return result
