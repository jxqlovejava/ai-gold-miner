"""Tests for signals/engine.py — ScoringEngine and DimensionWeights."""
from __future__ import annotations

import pytest

from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength
from gold_miner.signals.engine import DimensionWeights, ScoringEngine

_ZEROS = dict(
    technical=0.0, fundamental=0.0, news=0.0, sentiment=0.0,
    event=0.0, polymarket=0.0, anomaly=0.0, scenario=0.0, smart_money=0.0,
)


class TestDimensionWeights:
    def test_default_weights_sum_to_one(self) -> None:
        w = DimensionWeights()
        total = sum([
            w.technical, w.fundamental, w.news, w.sentiment,
            w.event, w.polymarket, w.anomaly, w.scenario, w.smart_money,
        ])
        assert abs(total - 1.0) < 0.001

    def test_default_weights_oil_merged_into_fundamental(self) -> None:
        """油价权重并入 fundamental；无独立 oil 字段."""
        w = DimensionWeights()
        assert not hasattr(w, "oil")
        assert w.fundamental == pytest.approx(0.30)

    def test_custom_valid_weights(self) -> None:
        w = DimensionWeights(
            technical=0.25, fundamental=0.25, news=0.20, sentiment=0.10,
            event=0.10, polymarket=0.05, anomaly=0.03, scenario=0.02, smart_money=0.0,
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


class TestHypeSuppression:
    """反带节奏对情绪面方向分的压制（2026-08-22 MECE 重构）."""

    def test_hype_suppression_factor_zero_without_hype(self) -> None:
        from gold_miner.signals.base import hype_suppression_factor

        assert hype_suppression_factor([]) == 0.0
        # 无 metadata.heuristic → 不算 hype 信号
        sigs = [
            Signal(name="大V加仓", dimension="sentiment", direction=SignalDirection.BULLISH,
                   strength=SignalStrength.MODERATE, score=0.5),
        ]
        assert hype_suppression_factor(sigs) == 0.0

    def test_hype_suppression_factor_scales_by_score(self) -> None:
        from gold_miner.signals.base import hype_suppression_factor

        sigs = [
            Signal(name="机构唱多做空信号", dimension="sentiment", direction=SignalDirection.BEARISH,
                   strength=SignalStrength.MODERATE, score=-0.4,
                   metadata={"heuristic": "walk_talk_mismatch"}),
        ]
        assert hype_suppression_factor(sigs) == pytest.approx(0.4)
        # 封顶 0.5
        strong = [
            Signal(name="标题党炒作过热", dimension="sentiment", direction=SignalDirection.BEARISH,
                   strength=SignalStrength.STRONG, score=-0.9,
                   metadata={"heuristic": "clickbait"}),
        ]
        assert hype_suppression_factor(strong) == pytest.approx(0.5)

    def test_score_applies_hype_suppression_to_sentiment(self) -> None:
        """检出 hype 时 sentiment 均分被 ×(1-factor) 压低，composite 随之降低."""
        def _bundle(with_hype: bool) -> SignalBundle:
            b = SignalBundle()
            b.add(Signal(name="大V加仓榜", dimension="sentiment", direction=SignalDirection.BULLISH,
                         strength=SignalStrength.STRONG, score=0.5))
            b.add(Signal(name="连续收阳", dimension="sentiment", direction=SignalDirection.BULLISH,
                         strength=SignalStrength.STRONG, score=0.5))
            if with_hype:
                b.add(Signal(name="机构唱多做空信号", dimension="sentiment", direction=SignalDirection.BEARISH,
                             strength=SignalStrength.MODERATE, score=-0.5,
                             metadata={"heuristic": "walk_talk_mismatch"}))
            return b

        engine = ScoringEngine()
        no_hype = engine.score(_bundle(with_hype=False))
        suppressed = engine.score(_bundle(with_hype=True))

        # 无 hype：单维 sentiment 全多，composite == dim_avg == 0.5
        assert no_hype.hype_suppression == 0.0
        assert no_hype.composite_score == pytest.approx(0.5)
        # 有 hype：factor = min(0.5, |−0.5|) = 0.5，sentiment 均分被压制 → composite 显著降低
        assert suppressed.hype_suppression == pytest.approx(0.5)
        assert suppressed.composite_score < no_hype.composite_score
        # 展示口径与 composite 一致（dimension_direction_summary 同样压低，均分四舍五入到 2 位）
        summary = _bundle(with_hype=True).dimension_direction_summary()
        assert summary["sentiment"]["avg_score"] == pytest.approx(
            round((0.5 * 2 + (-0.5)) / 3 * (1 - 0.5), 2)
        )
