"""测试价格预判生成器."""

import pytest

from gold_miner.intelligence.analyzer import ArticleAnalysis
from gold_miner.intelligence.forecaster import PriceForecaster


class TestPriceForecaster:
    def test_bullish_forecast(self):
        analysis = ArticleAnalysis(
            sentiment_score=0.6,
            sentiment_direction="bullish",
            bullish_count=8,
            bearish_count=2,
        )
        forecaster = PriceForecaster()
        forecast = forecaster.forecast(analysis)

        assert forecast.direction == "bullish"
        assert forecast.confidence > 0.3
        assert forecast.horizon_days > 0
        assert forecast.target_change_pct > 0

    def test_bearish_forecast(self):
        analysis = ArticleAnalysis(
            sentiment_score=-0.5,
            sentiment_direction="bearish",
            bullish_count=2,
            bearish_count=7,
        )
        forecaster = PriceForecaster()
        forecast = forecaster.forecast(analysis)

        assert forecast.direction == "bearish"
        assert forecast.target_change_pct < 0

    def test_suspicious_reduces_confidence(self):
        normal = ArticleAnalysis(
            sentiment_score=0.7,
            sentiment_direction="bullish",
            bullish_count=10,
            bearish_count=0,
            is_suspicious=False,
        )
        suspicious = ArticleAnalysis(
            sentiment_score=0.7,
            sentiment_direction="bullish",
            bullish_count=10,
            bearish_count=0,
            is_suspicious=True,
            manipulation_score=5,
            manipulation_flags=["测试标记"],
        )

        forecaster = PriceForecaster()
        f_normal = forecaster.forecast(normal)
        f_suspicious = forecaster.forecast(suspicious)

        assert f_suspicious.confidence < f_normal.confidence

    def test_cross_ref_adjusts_confidence(self):
        analysis = ArticleAnalysis(
            sentiment_score=0.6,
            sentiment_direction="bullish",
            bullish_count=8,
            bearish_count=2,
        )
        forecaster = PriceForecaster()

        # 交叉验证一致 → 置信度提升
        f_confirm = forecaster.forecast(analysis, cross_ref={
            "confirming": ["来源A", "来源B", "来源C"],
            "contradicting": [],
        })
        # 交叉验证矛盾 → 置信度降低
        f_contra = forecaster.forecast(analysis, cross_ref={
            "confirming": [],
            "contradicting": ["来源X", "来源Y"],
        })

        assert f_confirm.confidence > f_contra.confidence

    def test_llm_overrides_direction(self):
        analysis = ArticleAnalysis(
            sentiment_score=0.3,
            sentiment_direction="bullish",
            bullish_count=3,
            bearish_count=2,
        )
        llm = {"sentiment": "bearish", "confidence": 0.8, "reasoning": "深度分析显示短期承压"}

        forecaster = PriceForecaster()
        forecast = forecaster.forecast(analysis, llm_analysis=llm)

        assert forecast.direction == "bearish"

    def test_risk_factors_included(self):
        analysis = ArticleAnalysis(
            sentiment_score=0.3,
            sentiment_direction="bullish",
            bullish_count=2,
            bearish_count=1,
            is_suspicious=True,
            manipulation_score=4,
            manipulation_flags=["单一方向", "匿名来源"],
        )
        forecaster = PriceForecaster()
        forecast = forecaster.forecast(analysis)

        assert len(forecast.risk_factors) > 0

    def test_horizon_from_llm(self):
        analysis = ArticleAnalysis(
            sentiment_score=0.5,
            sentiment_direction="bullish",
            bullish_count=5,
            bearish_count=2,
        )
        llm = {"horizon_days": 30}

        forecaster = PriceForecaster()
        forecast = forecaster.forecast(analysis, llm_analysis=llm)

        assert forecast.horizon_days == 30

    def test_valid_until_set(self):
        analysis = ArticleAnalysis(
            sentiment_score=0.5,
            sentiment_direction="bullish",
            bullish_count=5,
            bearish_count=2,
        )
        forecaster = PriceForecaster()
        forecast = forecaster.forecast(analysis)

        assert forecast.valid_until is not None
        delta = (forecast.valid_until - forecast.created_at).days
        assert forecast.horizon_days - 1 <= delta <= forecast.horizon_days
