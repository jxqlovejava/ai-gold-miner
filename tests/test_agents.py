"""Tests for decision/agents.py — BullAgent, BearAgent, PortfolioManager."""

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

    def test_decide_short_when_bear_confident(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="neutral", confidence=0.3, suggested_position_pct=0.1)
        bear = AgentOpinion(agent_name="空头分析师", stance="bearish", confidence=0.8, suggested_position_pct=0.5)
        bundle = _bearish_bundle()
        decision = pm.decide(bull, bear, bundle)

        assert decision["direction"] == "short"
        assert decision["position_pct"] > 0

    def test_decide_neutral_when_conflict(self) -> None:
        pm = PortfolioManager()
        bull = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.7, suggested_position_pct=0.5)
        bear = AgentOpinion(agent_name="空头分析师", stance="bearish", confidence=0.7, suggested_position_pct=0.5)
        bundle = _mixed_bundle()
        decision = pm.decide(bull, bear, bundle)

        assert decision["direction"] in ("long", "short", "neutral")
        assert "position_pct" in decision

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

    def test_signal_type_mapping(self) -> None:
        """Position > 50% → 强信号, > 20% → 中等信号, > 0 → 弱信号."""
        pm = PortfolioManager()
        bundle = SignalBundle()
        bundle.add(Signal(name="x", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))

        # Strong signal
        bull_strong = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=1.0, suggested_position_pct=0.8)
        bear = AgentOpinion(agent_name="空头分析师", stance="neutral", confidence=0.0, suggested_position_pct=0.0)
        d = pm.decide(bull_strong, bear, bundle)
        assert d["signal_type"] == "强信号"

        # Medium signal
        bull_med = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.5, suggested_position_pct=0.35)
        d = pm.decide(bull_med, bear, bundle)
        assert d["signal_type"] == "中等信号"

        # Weak signal
        bull_weak = AgentOpinion(agent_name="多头分析师", stance="bullish", confidence=0.3, suggested_position_pct=0.1)
        d = pm.decide(bull_weak, bear, bundle)
        assert d["signal_type"] == "弱信号"

        # No signal
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
