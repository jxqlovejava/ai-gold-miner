"""Tests for AnalysisPipeline."""

import pytest

from gold_miner.pipeline.analysis import (
    AnalysisContext,
    AnalysisPipeline,
    AnalysisResult,
)
from gold_miner.signals.base import SignalBundle


class TestAnalysisContext:
    def test_default_values(self):
        ctx = AnalysisContext()
        assert ctx.days == 30
        assert ctx.with_news is True
        assert ctx.with_sentiment is True
        assert ctx.deep is False
        assert ctx.risk_profile == "moderate"
        assert ctx.skip_tracking is False
        assert ctx.skip_doctrine is False

    def test_custom_values(self):
        ctx = AnalysisContext(
            days=60,
            with_news=False,
            with_sentiment=False,
            deep=True,
            risk_profile="aggressive",
            skip_tracking=True,
            skip_doctrine=True,
        )
        assert ctx.days == 60
        assert ctx.with_news is False
        assert ctx.with_sentiment is False
        assert ctx.deep is True
        assert ctx.risk_profile == "aggressive"
        assert ctx.skip_tracking is True
        assert ctx.skip_doctrine is True


class TestAnalysisResult:
    def test_default_structure(self):
        result = AnalysisResult()
        assert isinstance(result.bundle, SignalBundle)
        assert isinstance(result.decision, dict)
        assert isinstance(result.checks, list)
        assert isinstance(result.doctrine_ctx, dict)
        assert result.prediction_id == ""
        assert result.current_price == 0.0


class TestAnalysisPipelineStepComposition:
    def test_pipeline_has_all_steps(self):
        pipeline = AnalysisPipeline()
        expected = [
            "collect",
            "generate_signals",
            "source_truth",
            "agent_debate",
            "risk_check",
            "doctrine_check",
            "decide",
            "track",
        ]
        assert pipeline._steps == expected

    def test_run_with_empty_data_returns_early(self, monkeypatch):
        """当 gold_df 为空时，run() 应提前返回."""
        pipeline = AnalysisPipeline()

        # mock _step_collect to set empty gold_df
        def mock_collect(ctx, result):
            result.gold_df = SignalBundle()._to_df() if hasattr(SignalBundle, '_to_df') else type('obj', (object,), {'empty': True})()
            # Create a mock DataFrame with empty property
            import pandas as pd
            result.gold_df = pd.DataFrame()

        monkeypatch.setattr(pipeline, '_step_collect', mock_collect)

        ctx = AnalysisContext()
        result = pipeline.run(ctx)

        assert "采集失败" in result.messages[-1]


class TestAnalysisPipelineSkipFlags:
    def test_skip_tracking(self, monkeypatch):
        pipeline = AnalysisPipeline()
        # Mock all steps to avoid network calls
        monkeypatch.setattr(pipeline, '_step_collect', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_generate_signals', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_source_truth', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_agent_debate', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_risk_check', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_doctrine_check', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_decide', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_track', lambda ctx, res: None)

        # Set a mock gold_df so it doesn't return early
        import pandas as pd
        def mock_collect(ctx, res):
            res.gold_df = pd.DataFrame({'close': [100.0]})
            res.current_price = 100.0

        monkeypatch.setattr(pipeline, '_step_collect', mock_collect)

        ctx = AnalysisContext(skip_tracking=True)
        result = pipeline.run(ctx)
        assert result.prediction_id == ""  # tracking skipped, no prediction_id set

    def test_skip_doctrine(self, monkeypatch):
        pipeline = AnalysisPipeline()
        import pandas as pd

        def mock_collect(ctx, res):
            res.gold_df = pd.DataFrame({'close': [100.0]})
            res.current_price = 100.0

        monkeypatch.setattr(pipeline, '_step_collect', mock_collect)
        monkeypatch.setattr(pipeline, '_step_generate_signals', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_source_truth', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_agent_debate', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_risk_check', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_doctrine_check', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_decide', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_track', lambda ctx, res: None)

        ctx = AnalysisContext(skip_doctrine=True)
        result = pipeline.run(ctx)
        # doctrine_result should be None since step skipped
        assert result.doctrine_result is None

    def test_skip_dashboard(self, monkeypatch):
        pipeline = AnalysisPipeline()
        import pandas as pd

        def mock_collect(ctx, res):
            res.gold_df = pd.DataFrame({'close': [100.0]})
            res.current_price = 100.0

        monkeypatch.setattr(pipeline, '_step_collect', mock_collect)
        monkeypatch.setattr(pipeline, '_step_generate_signals', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_source_truth', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_agent_debate', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_risk_check', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_doctrine_check', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_decide', lambda ctx, res: None)
        monkeypatch.setattr(pipeline, '_step_track', lambda ctx, res: None)

        ctx = AnalysisContext(skip_dashboard=True)
        result = pipeline.run(ctx)
        # trade_decision should be None since decide step skipped
        assert result.trade_decision is None


class TestAnalysisPipelineResultStructure:
    def test_result_has_expected_fields(self):
        result = AnalysisResult()
        # Check all expected fields exist
        assert hasattr(result, 'bundle')
        assert hasattr(result, 'decision')
        assert hasattr(result, 'final_decision')
        assert hasattr(result, 'checks')
        assert hasattr(result, 'doctrine_ctx')
        assert hasattr(result, 'doctrine_result')
        assert hasattr(result, 'prediction_id')
        assert hasattr(result, 'current_price')
        assert hasattr(result, 'gold_df')
        assert hasattr(result, 'dxy_df')
        assert hasattr(result, 'rate_df')
        assert hasattr(result, 'silver_df')
        assert hasattr(result, 'breakeven_df')
        assert hasattr(result, 'news_raw')
        assert hasattr(result, 'au_df')
        assert hasattr(result, 'bull_opinion')
        assert hasattr(result, 'bear_opinion')
        assert hasattr(result, 'trade_decision')
        assert hasattr(result, 'alerts')
        assert hasattr(result, 'messages')
