"""Tests for AnalysisPipeline."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from gold_miner.doctrine.checker import DoctrineChecker
from gold_miner.pipeline.analysis import (
    AnalysisContext,
    AnalysisPipeline,
    AnalysisResult,
)
from gold_miner.signals.base import Signal, SignalBundle, SignalDirection, SignalStrength


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
            "munger_models",
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


class TestAnalysisPipelineOutputSections:
    """测试 _step_decide 新增的军规、Munger、画像输出."""

    def test_select_munger_models_returns_up_to_three(self):
        from gold_miner.doctrine.munger_models import GOLD_MODELS

        bundle = SignalBundle()
        bundle.add(
            Signal(
                name="情绪极端",
                dimension="sentiment",
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.MODERATE,
                score=0.5,
            )
        )
        pipeline = AnalysisPipeline()
        models = pipeline._select_munger_models(bundle, count=3)
        assert len(models) <= 3
        if GOLD_MODELS:
            assert len(models) > 0
            assert all(hasattr(m, "name_cn") for m in models)
        else:
            assert models == []

    def test_select_munger_models_falls_back_to_classics_when_empty(self):
        from gold_miner.doctrine.munger_models import GOLD_MODELS

        if not GOLD_MODELS:
            pytest.skip("GOLD_MODELS 未加载，跳过兜底测试")

        bundle = SignalBundle()
        pipeline = AnalysisPipeline()
        models = pipeline._select_munger_models(bundle, count=3)
        names = {m.name_cn for m in models}
        # 空信号时应命中通用兜底模型
        assert any(name in names for name in {"安全边际", "市场先生", "能力圈"})

    def test_select_munger_models_prefers_keyword_matches(self):
        from gold_miner.doctrine.munger_models import GOLD_MODELS

        if not GOLD_MODELS:
            pytest.skip("GOLD_MODELS 未加载，跳过关键词匹配测试")

        bundle = SignalBundle()
        bundle.add(
            Signal(
                name="社会认同倾向",
                dimension="sentiment",
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.STRONG,
                score=-0.8,
            )
        )
        pipeline = AnalysisPipeline()
        models = pipeline._select_munger_models(bundle, count=3)
        names = {m.name_cn for m in models}
        # "社会认同" 2-gram 应命中社会认同倾向
        assert any("社会认同" in n for n in names)

    def test_print_doctrine_checklist_icons(self, capsys):
        pipeline = AnalysisPipeline()
        result = AnalysisResult()
        decision = {"position_pct": 0.25}
        doctrine = DoctrineChecker().check(decision, {})
        result.doctrine_result = doctrine
        result.final_decision = decision

        pipeline._print_doctrine_checklist(result)
        captured = capsys.readouterr().out

        assert "投资军规自查 (r001-r030)" in captured
        assert "r001" in captured
        assert "r030" in captured
        # r001 仓位 25% > 20% 应标记 ❌
        assert "❌ r001" in captured
        # r015 是 info 级别，默认通过
        assert "✅ r015" in captured

    def test_print_doctrine_checklist_handles_no_doctrine(self, capsys):
        pipeline = AnalysisPipeline()
        result = AnalysisResult()
        result.doctrine_result = None

        pipeline._print_doctrine_checklist(result)
        captured = capsys.readouterr().out
        assert "军规检查未执行" in captured

    def test_load_investor_data_fallback(self, monkeypatch, tmp_path):
        pipeline = AnalysisPipeline()

        class FakeStore:
            def load_investor_profile(self):
                return ""

            def load_portfolio(self):
                return {}

        monkeypatch.setattr(
            "gold_miner.pipeline.analysis.get_store", lambda private_data_dir=None: FakeStore()
        )

        result = AnalysisResult()
        profile, portfolio, warnings = pipeline._load_investor_data(result)

        assert any("使用示例投资者画像" in w for w in warnings)
        assert any("使用示例持仓数据" in w for w in warnings)
        assert "平衡型" in profile or "风险偏好" in profile
        assert portfolio.get("limits", {}).get("risk_profile") == "balanced"
        assert result.investor_profile == profile
        assert result.portfolio == portfolio

    def test_print_profile_match_compatible(self, capsys):
        pipeline = AnalysisPipeline()
        result = AnalysisResult()
        result.current_price = 100.0
        result.final_decision = {"position_pct": 0.10}
        portfolio = {
            "limits": {
                "total_funds": 200000,
                "max_gold_pct": 80,
                "max_single_pct": 20,
                "risk_profile": "balanced",
                "investment_horizon": "1-3年",
            },
            "positions": {
                "gold_example": {
                    "instrument": "积存金",
                    "grams": 100.0,
                }
            },
        }

        pipeline._print_profile_match(result, "profile text", portfolio)
        captured = capsys.readouterr().out

        assert "画像匹配" in captured
        assert "风险画像: balanced" in captured
        assert "持仓周期: 1-3年" in captured
        assert "建议符合画像约束 ✅" in captured

    def test_print_profile_match_exceeds_limits(self, capsys):
        pipeline = AnalysisPipeline()
        result = AnalysisResult()
        result.current_price = 100.0
        result.final_decision = {"position_pct": 0.30}
        portfolio = {
            "limits": {
                "total_funds": 200000,
                "max_gold_pct": 80,
                "max_single_pct": 20,
                "risk_profile": "balanced",
                "investment_horizon": "1-3年",
            },
            "positions": {
                "gold_example": {
                    "instrument": "积存金",
                    "grams": 100.0,
                }
            },
        }

        pipeline._print_profile_match(result, "profile text", portfolio)
        captured = capsys.readouterr().out

        assert "建议仓位: 30% vs 单品种上限 20% — 超出 ⚠️" in captured
        assert "建议部分超出画像约束 ⚠️" in captured

    def test_print_profile_match_empty_portfolio(self, capsys):
        pipeline = AnalysisPipeline()
        result = AnalysisResult()
        pipeline._print_profile_match(result, "", {})
        captured = capsys.readouterr().out
        assert "未找到投资者持仓数据" in captured


class TestMinshengAccumulationPrice:
    """测试民生银行积存金价格在数据采集步骤中被抓取并记录."""

    def test_fetch_minsheng_price_success(self, monkeypatch):
        from gold_miner.data.jd_accumulation_gold import JdGoldPrice

        class FakeFetcher:
            def __init__(self, bank: str) -> None:
                self.bank = bank

            def fetch_price(self):
                return JdGoldPrice(
                    timestamp=datetime.now(),
                    product_name="民生积存金",
                    price=918.50,
                    change_pct="+0.30%",
                    source="jd.com",
                )

        monkeypatch.setattr(
            "gold_miner.pipeline.analysis.JdAccumulationGoldFetcher", FakeFetcher
        )
        pipeline = AnalysisPipeline()
        price = pipeline._fetch_minsheng_accumulation_price()

        assert price is not None
        assert price.price == 918.50
        assert price.change_pct == "+0.30%"

    def test_fetch_minsheng_price_failure_returns_none(self, monkeypatch):
        class FakeFetcher:
            def __init__(self, bank: str) -> None:
                pass

            def fetch_price(self):
                raise RuntimeError("network")

        monkeypatch.setattr(
            "gold_miner.pipeline.analysis.JdAccumulationGoldFetcher", FakeFetcher
        )
        pipeline = AnalysisPipeline()
        assert pipeline._fetch_minsheng_accumulation_price() is None

    def test_step_collect_records_minsheng_price(self, monkeypatch):
        pipeline = AnalysisPipeline()

        # Mock 现货黄金数据
        class FakeSpotFetcher:
            def fetch(self, **kwargs):
                return pd.DataFrame({"close": [800.0]})

            def fetch_international_quote(self):
                return [{"price": 3300.0, "name": "伦敦金"}]

        class FakeMacroFetcher:
            def fetch_dxy(self):
                return pd.DataFrame()

            def fetch_real_rate(self):
                return pd.DataFrame()

            def fetch_silver(self):
                return pd.DataFrame()

            def fetch_breakeven(self):
                return pd.DataFrame()

        class FakeAlert:
            def check_all(self, **kwargs):
                return []

        monkeypatch.setattr(
            "gold_miner.pipeline.analysis.SpotGoldFetcher", FakeSpotFetcher
        )
        monkeypatch.setattr(
            "gold_miner.pipeline.analysis.MacroDataFetcher", FakeMacroFetcher
        )
        monkeypatch.setattr("gold_miner.pipeline.analysis.PriceAlert", FakeAlert)

        def mock_fetch_minsheng(self):
            from gold_miner.data.jd_accumulation_gold import JdGoldPrice

            return JdGoldPrice(
                timestamp=datetime.now(),
                product_name="民生积存金",
                price=920.0,
                change_pct="+0.10%",
                source="jd.com",
            )

        monkeypatch.setattr(
            AnalysisPipeline, "_fetch_minsheng_accumulation_price", mock_fetch_minsheng
        )

        ctx = AnalysisContext()
        result = AnalysisResult()
        pipeline._step_collect(ctx, result)

        assert result.minsheng_accumulation_price == 920.0
        assert result.minsheng_accumulation_change_pct == "+0.10%"
