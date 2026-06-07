"""Tests for signals/engine.py — ScoringEngine and DimensionWeights."""

import pytest

from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength
from gold_miner.signals.engine import DimensionWeights, ScoringEngine

_ZEROS = dict(
    technical=0.0, fundamental=0.0, news=0.0, sentiment=0.0,
    event=0.0, polymarket=0.0, anomaly=0.0, scenario=0.0,
)


class TestDimensionWeights:
    def test_default_weights_sum_to_one(self) -> None:
        w = DimensionWeights()
        total = sum([
            w.technical, w.fundamental, w.news, w.sentiment,
            w.event, w.polymarket, w.anomaly, w.scenario,
        ])
        assert abs(total - 1.0) < 0.001

    def test_custom_valid_weights(self) -> None:
        w = DimensionWeights(
            technical=0.25, fundamental=0.25, news=0.20, sentiment=0.10,
            event=0.10, polymarket=0.05, anomaly=0.03, scenario=0.02,
        )
        assert abs(w.technical - 0.25) < 0.001

    def test_raises_when_total_not_one(self) -> None:
        with pytest.raises(ValueError, match="权重之和必须等于1"):
            DimensionWeights(**{**_ZEROS, "technical": 0.5})

    def test_raises_when_total_exceeds_one(self) -> None:
        with pytest.raises(ValueError, match="权重之和必须等于1"):
            DimensionWeights(
                technical=0.6, fundamental=0.3, news=0.3, sentiment=0.2,
                event=0.1, polymarket=0.1, anomaly=0.1, scenario=0.1,
            )


class TestScoringEngine:
    def test_score_empty_bundle_returns_zero(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        result = engine.score(bundle)
        assert result.composite_score == 0.0
        assert result.confidence == 0.0

    def test_score_single_bullish_signal_positive(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        bundle.add(Signal(
            name="RSI超卖", dimension="technical",
            direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE,
            score=0.6,
        ))
        result = engine.score(bundle)
        assert result.composite_score > 0

    def test_score_single_bearish_signal_negative(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        bundle.add(Signal(
            name="RSI超买", dimension="technical",
            direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE,
            score=-0.6,
        ))
        result = engine.score(bundle)
        assert result.composite_score < 0

    def test_score_mixed_signals_reflects_balance(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        bundle.add(Signal(
            name="Bullish MACD", dimension="technical",
            direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG,
            score=0.6,
        ))
        bundle.add(Signal(
            name="Bearish News", dimension="news",
            direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE,
            score=-0.4,
        ))
        result = engine.score(bundle)
        assert -0.3 < result.composite_score < 0.3
        assert result.confidence > 0

    def test_score_clamps_to_neg_one_to_one(self) -> None:
        engine = ScoringEngine(weights=DimensionWeights(**{**_ZEROS, "technical": 1.0}))
        bundle = SignalBundle()
        bundle.add(Signal(
            name="Extreme", dimension="technical",
            direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG,
            score=5.0,
        ))
        result = engine.score(bundle)
        assert result.composite_score <= 1.0
        assert result.composite_score >= -1.0

    def test_score_increases_confidence_with_alignment(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        for _ in range(4):
            bundle.add(Signal(
                name="Bullish", dimension="technical",
                direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE,
                score=0.5,
            ))
        result = engine.score(bundle)
        assert result.confidence > 0.5

    def test_recommend_buy_when_score_above_threshold(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        bundle.add(Signal(name="Bullish", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.8))
        bundle.add(Signal(name="Bullish News", dimension="news", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.5))
        bundle.add(Signal(name="Bullish Sentiment", dimension="sentiment", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.4))
        bundle.add(Signal(name="Bullish Fundamental", dimension="fundamental", direction=SignalDirection.BULLISH, strength=SignalStrength.MODERATE, score=0.3))
        bundle.add(Signal(name="Bullish Technical2", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.WEAK, score=0.2))
        engine.score(bundle)
        recommendation = engine.recommend(bundle)
        assert recommendation["action"] == "buy"

    def test_recommend_sell_when_score_below_threshold(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        for _ in range(5):
            bundle.add(Signal(name="Bearish", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.6))
        engine.score(bundle)
        recommendation = engine.recommend(bundle)
        assert recommendation["action"] == "sell"

    def test_recommend_hold_when_score_near_zero(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        bundle.add(Signal(name="Neutral", dimension="technical", direction=SignalDirection.NEUTRAL, strength=SignalStrength.WEAK, score=0.05))
        engine.score(bundle)
        recommendation = engine.recommend(bundle)
        assert recommendation["action"] == "hold"

    def test_recommend_hold_when_confidence_low(self) -> None:
        engine = ScoringEngine()
        bundle = SignalBundle()
        bundle.add(Signal(name="Bullish", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.5))
        bundle.add(Signal(name="Bearish", dimension="news", direction=SignalDirection.BEARISH, strength=SignalStrength.MODERATE, score=-0.5))
        engine.score(bundle)
        recommendation = engine.recommend(bundle)
        assert recommendation["action"] == "hold"

    def test_recommend_buy_high_urgency(self) -> None:
        engine = ScoringEngine(weights=DimensionWeights(**{**_ZEROS, "technical": 1.0}))
        bundle = SignalBundle()
        for _ in range(6):
            bundle.add(Signal(name="Strong Bullish", dimension="technical", direction=SignalDirection.BULLISH, strength=SignalStrength.STRONG, score=0.9))
        engine.score(bundle)
        recommendation = engine.recommend(bundle, threshold_buy=0.3)
        assert recommendation["action"] == "buy"
        assert recommendation["urgency"] == "high"

    def test_recommend_sell_high_urgency(self) -> None:
        engine = ScoringEngine(weights=DimensionWeights(**{**_ZEROS, "technical": 1.0}))
        bundle = SignalBundle()
        for _ in range(6):
            bundle.add(Signal(name="Strong Bearish", dimension="technical", direction=SignalDirection.BEARISH, strength=SignalStrength.STRONG, score=-0.9))
        engine.score(bundle)
        recommendation = engine.recommend(bundle, threshold_sell=-0.3)
        assert recommendation["action"] == "sell"
        assert recommendation["urgency"] == "high"
