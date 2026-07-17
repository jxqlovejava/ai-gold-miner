"""多Agent辩论系统 — 多头 vs 空头 vs 风控."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gold_miner.signals.base import SignalBundle
from gold_miner.strategy.kelly import kelly_position


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


def _signal_type_from_final(position_pct: float, abs_score: float) -> str:
    """按最终可执行仓位 + 综合评分标注信号（Kelly/阈值过滤之后）.

    Kelly 硬上限约 20%，单靠 position_pct 几乎到不了「强信号」，
    故与 |composite_score| 联合判定可执行强度。
    """
    if position_pct <= 0 or abs_score < 0.3:
        return "无信号"
    if position_pct > 0.5 or abs_score >= 0.7:
        return "强信号"
    if position_pct > 0.2 or abs_score >= 0.5:
        return "中等信号"
    return "弱信号"


class PortfolioManager:
    NAME = "投资经理"

    # 与 ScoringEngine.recommend 一致：|score|<0.3 不作为方向性交易
    SCORE_THRESHOLD = 0.3

    def decide(
        self,
        bull: AgentOpinion,
        bear: AgentOpinion,
        bundle: SignalBundle,
        risk_profile: str = "moderate",
        strategy_decision: Any | None = None,
        long_only: bool = True,
    ) -> dict[str, Any]:
        risk_multipliers = {"aggressive": 1.2, "moderate": 1.0, "conservative": 0.6}
        multiplier = risk_multipliers.get(risk_profile, 1.0)
        score = bundle.composite_score

        if bull.stance == "bullish" and bear.stance != "bearish":
            direction = "long"
            raw_position = bull.suggested_position_pct
        elif bear.stance == "bearish" and bull.stance != "bullish":
            # 内部可记 bearish_bias；long_only 时执行方向不为 short
            direction = "short"
            raw_position = bear.suggested_position_pct
        else:
            net_score = score
            if net_score > 0.2:
                direction = "long"
                raw_position = abs(net_score)
            elif net_score < -0.2:
                direction = "short"
                raw_position = abs(net_score)
            else:
                direction = "neutral"
                raw_position = 0.0

        bearish_bias = direction == "short"
        position_pct = min(raw_position * multiplier, 0.9)

        # Kelly 仓位参考（做多边缘才给出正仓；偏空时 suggested 常为 0）
        kelly = kelly_position(
            composite_score=score,
            confidence=bundle.confidence,
        )
        original_pos = position_pct
        if direction == "long":
            position_pct = (
                min(position_pct, kelly.suggested_pct)
                if kelly.is_actionable()
                else min(position_pct, 0.05)
            )
        elif direction == "short":
            # 空头意图仅保留减仓参考强度；Kelly 不做空
            position_pct = min(position_pct, 0.5)
        else:
            position_pct = 0.0

        # 弱综合分：不给方向性开仓；微弱负分≠减仓信号（须 |score|≥阈值）
        weak_score = abs(score) < self.SCORE_THRESHOLD
        if weak_score:
            direction = "neutral"
            position_pct = 0.0
            # 仅显著偏空才保留减仓意图，避免 -0.04 噪声触发 reduce
            bearish_bias = score <= -self.SCORE_THRESHOLD

        # long_only：永不返回 short；显著偏空由 bearish_bias 交给 position_state 决定是否减仓
        if long_only and direction == "short":
            direction = "neutral"
            position_pct = 0.0
            if not weak_score:
                bearish_bias = True

        result = {
            "direction": direction,
            "position_pct": round(position_pct, 2),
            "signal_type": _signal_type_from_final(position_pct, abs(score)),
            "risk_profile": risk_profile,
            "bull_confidence": round(bull.confidence, 2),
            "bear_confidence": round(bear.confidence, 2),
            "composite_score": round(score, 2),
            "long_only": long_only,
            "bearish_bias": bearish_bias,
            "kelly": {
                "raw": kelly.raw_kelly,
                "quarter": kelly.quarter_kelly,
                "suggested": kelly.suggested_pct,
                "edge": kelly.edge,
                "rationale": kelly.rationale,
            },
            "debate_summary": {
                "bull_args": bull.arguments,
                "bear_args": bear.arguments,
            },
        }
        if (
            direction == "long"
            and kelly.is_actionable()
            and kelly.suggested_pct < original_pos
        ):
            result["kelly_override"] = (
                f"Kelly 压降: {original_pos:.0%} → {kelly.suggested_pct:.0%}"
            )

        # 策略目标覆盖（弱分时仅允许 long/neutral 且需 position>0 才覆盖）
        if strategy_decision is not None and strategy_decision.position_pct > 0:
            strat_dir = strategy_decision.direction
            if long_only and strat_dir == "short":
                strat_dir = "neutral"
            if weak_score and abs(score) < self.SCORE_THRESHOLD:
                # 弱分：策略不得强行 long/short 开仓；仅记录策略元数据
                result["strategy_objective"] = strategy_decision.objective.value
                result["strategy_reason"] = (
                    f"[弱分未覆盖] {strategy_decision.reason}"
                )
                result["stop_loss"] = strategy_decision.stop_loss
                result["take_profit_levels"] = strategy_decision.take_profit_levels
                result["tp_weights"] = strategy_decision.tp_weights
            else:
                result["direction"] = strat_dir
                result["position_pct"] = round(
                    min(strategy_decision.position_pct, max(position_pct, 0.01) * 1.1),
                    2,
                )
                if long_only and result["direction"] == "short":
                    result["direction"] = "neutral"
                result["strategy_objective"] = strategy_decision.objective.value
                result["strategy_reason"] = strategy_decision.reason
                result["stop_loss"] = strategy_decision.stop_loss
                result["take_profit_levels"] = strategy_decision.take_profit_levels
                result["tp_weights"] = strategy_decision.tp_weights

        # 最终再保 long_only + 阈值，并按最终仓位重算 signal_type
        if long_only and result["direction"] == "short":
            result["direction"] = "neutral"
        final_score = abs(float(result["composite_score"]))
        if final_score < self.SCORE_THRESHOLD and result.get("strategy_objective") is None:
            result["direction"] = "neutral"
            result["position_pct"] = 0.0
        elif (
            final_score < self.SCORE_THRESHOLD
            and result.get("strategy_reason", "").startswith("[弱分未覆盖]")
        ):
            result["direction"] = "neutral"
            result["position_pct"] = 0.0

        result["position_pct"] = round(float(result["position_pct"]), 2)
        result["signal_type"] = _signal_type_from_final(
            float(result["position_pct"]),
            final_score,
        )
        return result
