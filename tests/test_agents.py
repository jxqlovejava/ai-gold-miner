"""Tests for decision/agents.py — BullAgent, BearAgent, PortfolioManager."""
from __future__ import annotations

from gold_miner.decision.agents import AgentOpinion, BearAgent, BullAgent, PortfolioManager
from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


def _bullish_bundle() -> SignalBundle:
    """A bundle dominated by bullish signals."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="RSI超卖", dimension="technical",
        direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.6,
    ))
    bundle.add(Signal(
        name="MACD金叉", dimension="technical",
        direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.8,
    ))
    bundle.add(Signal(
        name="布林带下轨", dimension="technical",
        direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.3,
    ))
    bundle.add(Signal(
        name="利空消息", dimension="news",
        direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.2,
    ))
    bundle.composite_score = 0.55
    bundle.confidence = 0.75
    return bundle


def _bearish_bundle() -> SignalBundle:
    """A bundle dominated by bearish signals."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="RSI超买", dimension="technical",
        direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.6,
    ))
    bundle.add(Signal(
        name="MACD死叉", dimension="technical",
        direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.8,
    ))
    bundle.add(Signal(
        name="布林带上轨", dimension="technical",
        direction=SignalDirection.BEARISH, strength=SignalStrength.WEAK, score=-0.3,
    ))
    bundle.add(Signal(
        name="利多消息", dimension="news",
        direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.2,
    ))
    bundle.composite_score = -0.55
    bundle.confidence = 0.75
    return bundle


def _mixed_bundle() -> SignalBundle:
    """A bundle with balanced bullish and bearish signals."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="RSI超卖", dimension="technical",
        direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.5,
    ))
    bundle.add(Signal(
        name="MACD死叉", dimension="technical",
        direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.7,
    ))
    bundle.composite_score = -0.1
    bundle.confidence = 0.5
    return bundle


def _weak_long_bundle() -> SignalBundle:
    """Weak positive composite — must not become a buy/long allocation."""
    bundle = SignalBundle()
    bundle.add(Signal(
        name="轻微利多", dimension="news",
        direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.1,
    ))
    bundle.composite_score = 0.07
    bundle.confidence = 0.5
    return bundle


class TestBullAgent:
    def test_analyze_returns_agent_opinion(self) -> None:
        agent = BullAgent()
        bundle = _bullish_bundle()
        opinion = agent.analyze(bundle)
        assert isinstance(opinion, AgentOpinion)
        assert opinion.agent_name == BullAgent.NAME

    def test_analyze_bullish_bundle_returns_bullish_stance(self) -> None:
        agent = BullAgent()
        bundle = _bullish_bundle()
        opinion = agent.analyze(bundle)
        assert opinion.stance == "bullish"

    def test_analyze_bullish_bundle_high_confidence(self) -> None:
        agent = BullAgent()
        bundle = _bullish_bundle()
        opinion = agent.analyze(bundle)
        assert opinion.confidence > 0.55

    def test_analyze_bearish_bundle_low_confidence(self) -> None:
        """BullAgent analyzing bearish data should have low confidence."""
        agent = BullAgent()
        bundle = _bearish_bundle()
        opinion = agent.analyze(bundle)
        # Bullish signals exist (one bullish news), but bearish dominate
        assert opinion.confidence < 0.5 or opinion.stance == "neutral"

    def test_analyze_includes_arguments(self) -> None:
        agent = BullAgent()
        bundle = _bullish_bundle()
        opinion = agent.analyze(bundle)
        assert len(opinion.arguments) > 0
        assert all(isinstance(arg, str) for arg in opinion.arguments)

    def test_suggested_position_pct_is_reasonable(self) -> None:
        agent = BullAgent()
        bundle = _bullish_bundle()
        opinion = agent.analyze(bundle)
        assert 0.0 <= opinion.suggested_position_pct <= 0.8


class TestBearAgent:
    def test_analyze_returns_agent_opinion(self) -> None:
        agent = BearAgent()
        bundle = _bearish_bundle()
        opinion = agent.analyze(bundle)
        assert isinstance(opinion, AgentOpinion)

    def test_analyze_bearish_bundle_returns_bearish_stance(self) -> None:
        agent = BearAgent()
        bundle = _bearish_bundle()
        opinion = agent.analyze(bundle)
        assert opinion.stance == "bearish"

    def test_analyze_bearish_bundle_high_confidence(self) -> None:
        agent = BearAgent()
        bundle = _bearish_bundle()
        opinion = agent.analyze(bundle)
        assert opinion.confidence > 0.55

    def test_analyze_bullish_bundle_low_confidence(self) -> None:
        agent = BearAgent()
        bundle = _bullish_bundle()
        opinion = agent.analyze(bundle)
        assert opinion.confidence < 0.5 or opinion.stance == "neutral"

    def test_includes_arguments(self) -> None:
        agent = BearAgent()
        bundle = _bearish_bundle()
        opinion = agent.analyze(bundle)
        assert len(opinion.arguments) > 0


class TestPortfolioManager:
    def test_decide_long_when_bull_confident(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.8, suggested_position_pct=0.5)
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bundle = _bullish_bundle()
        decision = pm.decide(bull, bear, bundle)

        assert decision["direction"] == "long"
        assert decision["position_pct"] > 0

    def test_decide_short_when_bear_confident_long_only_false(self) -> None:
        """long_only=False 才允许 direction=short."""
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bear = AgentOpinion(agent_name="空头分析师", stance="bearish", confidence=0.8, suggested_position_pct=0.5)
        bundle = _bearish_bundle()
        decision = pm.decide(bull, bear, bundle, long_only=False)

        assert decision["direction"] == "short"
        assert decision["position_pct"] > 0
        assert decision["bearish_bias"] is True

    def test_long_only_never_returns_short(self) -> None:
        """默认 long_only=True：偏空 bundle 不得输出 short."""
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bear = AgentOpinion(agent_name="空头分析师", stance="bearish", confidence=0.8, suggested_position_pct=0.5)
        bundle = _bearish_bundle()
        decision = pm.decide(bull, bear, bundle, long_only=True)

        assert decision["direction"] != "short"
        assert decision["direction"] == "neutral"
        assert decision["position_pct"] == 0
        assert decision["bearish_bias"] is True
        assert decision["long_only"] is True

    def test_weak_score_forces_neutral_zero_position(self) -> None:
        """弱综合分（如 +0.07）不得变成 long/buy 仓位."""
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.6, suggested_position_pct=0.3)
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bundle = _weak_long_bundle()
        decision = pm.decide(bull, bear, bundle)

        assert decision["direction"] == "neutral"
        assert decision["position_pct"] == 0
        assert decision["signal_type"] == "无信号"

    def test_decide_neutral_when_conflict(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.7, suggested_position_pct=0.5)
        bear = AgentOpinion(agent_name="空头分析师", stance="bearish", confidence=0.7, suggested_position_pct=0.5)
        bundle = _mixed_bundle()
        decision = pm.decide(bull, bear, bundle)

        assert decision["direction"] in ("long", "short", "neutral")
        assert "position_pct" in decision
        # 弱分冲突 → 默认 long_only 下应为 neutral
        assert decision["direction"] == "neutral"
        assert decision["position_pct"] == 0

    def test_aggressive_risk_profile_increases_position(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.8, suggested_position_pct=0.5)
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bundle = _bullish_bundle()

        aggressive = pm.decide(bull, bear, bundle, risk_profile="aggressive")
        moderate = pm.decide(bull, bear, bundle, risk_profile="moderate")

        assert aggressive["position_pct"] >= moderate["position_pct"]

    def test_conservative_risk_profile_reduces_position(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.8, suggested_position_pct=0.5)
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bundle = _bullish_bundle()

        conservative = pm.decide(bull, bear, bundle, risk_profile="conservative")
        moderate = pm.decide(bull, bear, bundle, risk_profile="moderate")

        assert conservative["position_pct"] <= moderate["position_pct"]

    def test_position_capped_at_90_percent(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=1.0, suggested_position_pct=0.9)
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.0, suggested_position_pct=0.0)
        bundle = _bullish_bundle()

        decision = pm.decide(bull, bear, bundle, risk_profile="aggressive")
        assert decision["position_pct"] <= 0.9

    def test_signal_type_from_final_after_kelly(self) -> None:
        """signal_type 必须基于 Kelly/阈值后的最终仓位，而非 raw suggested."""
        pm = PortfolioManager()
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.0, suggested_position_pct=0.0)

        # 强分 + 最终有仓 → 强信号（|score|>=0.7 或仓位很大）
        bundle_strong = SignalBundle()
        bundle_strong.composite_score = 0.85
        bundle_strong.confidence = 0.9
        bull_strong = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=1.0, suggested_position_pct=0.8)
        d = pm.decide(bull_strong, bear, bundle_strong)
        assert d["direction"] == "long"
        assert d["position_pct"] > 0
        assert d["signal_type"] == "强信号"

        # 中等分
        bundle_med = SignalBundle()
        bundle_med.composite_score = 0.55
        bundle_med.confidence = 0.7
        bull_med = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.7, suggested_position_pct=0.35)
        d = pm.decide(bull_med, bear, bundle_med)
        assert d["signal_type"] == "中等信号"

        # 刚过阈值的弱可执行
        bundle_weak = SignalBundle()
        bundle_weak.composite_score = 0.35
        bundle_weak.confidence = 0.6
        bull_weak = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.5, suggested_position_pct=0.15)
        d = pm.decide(bull_weak, bear, bundle_weak)
        assert d["signal_type"] == "弱信号"

        # 无边缘
        bull_none = AgentOpinion(agent_name="多头分析师", stance="neutral", confidence=0.0, suggested_position_pct=0.0)
        bundle_empty = SignalBundle()
        d = pm.decide(bull_none, bear, bundle_empty)
        assert d["signal_type"] == "无信号"

    def test_decide_contains_debate_summary(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.8, suggested_position_pct=0.5,
                            arguments=["看多理由1", "看多理由2"])
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1,
                            arguments=["看空理由1"])
        bundle = _bullish_bundle()
        decision = pm.decide(bull, bear, bundle)

        assert "debate_summary" in decision
        assert decision["debate_summary"]["bull_args"] == ["看多理由1", "看多理由2"]
        assert decision["debate_summary"]["bear_args"] == ["看空理由1"]
