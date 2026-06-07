"""情景分析模块单元测试."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from gold_miner.scenarios.analyzer import ScenarioAnalyzer
from gold_miner.scenarios.models import (
    HistoricalAnalog,
    ImpactChannel,
    PriceImpactEstimate,
    ScenarioReport,
    StrategyRecommendation,
)
from gold_miner.scenarios.store import ScenarioStore


class TestImpactChannel:
    def test_create_channel(self) -> None:
        c = ImpactChannel(
            channel="避险需求",
            direction="bullish",
            magnitude="strong",
            description="全球避险资金涌入黄金",
            timeframe="immediate",
        )
        assert c.channel == "避险需求"
        assert c.direction == "bullish"
        assert c.magnitude == "strong"


class TestHistoricalAnalog:
    def test_create_analog(self) -> None:
        a = HistoricalAnalog(
            event_name="2008金融危机",
            period="2008-2009",
            gold_price_change_pct=25.0,
            similarity_score=0.7,
            key_parallels=["系统性风险", "央行大规模宽松"],
            key_differences=["当时金价基数较低"],
        )
        assert a.event_name == "2008金融危机"
        assert a.gold_price_change_pct == 25.0
        assert len(a.key_parallels) == 2


class TestPriceImpactEstimate:
    def test_create_estimate(self) -> None:
        pi = PriceImpactEstimate(
            direction="bullish",
            base_case_change_pct=15.0,
            bullish_case_change_pct=30.0,
            bearish_case_change_pct=-5.0,
            peak_impact_months=6,
            confidence=0.65,
            reasoning="历史数据表明避险需求会推高金价",
        )
        assert pi.direction == "bullish"
        assert pi.confidence == 0.65


class TestScenarioReport:
    def test_create_report(self) -> None:
        report = ScenarioReport(
            id="test123",
            scenario_description="美债危机爆发",
            time_horizon_months=12,
        )
        assert report.id == "test123"
        assert report.time_horizon_months == 12

    def test_summary_with_price_impact(self) -> None:
        report = ScenarioReport(
            id="test123",
            scenario_description="全球美债危机爆发，美元信用受损",
            time_horizon_months=12,
            price_impact=PriceImpactEstimate(
                direction="bullish",
                base_case_change_pct=20.0,
                confidence=0.7,
                peak_impact_months=6,
            ),
        )
        summary = report.summary
        assert "看涨" in summary
        assert "+20.0%" in summary

    def test_summary_without_price_impact(self) -> None:
        report = ScenarioReport(
            id="test123",
            scenario_description="测试情景",
        )
        summary = report.summary
        assert "测试情景" in summary


class TestScenarioAnalyzer:
    def test_fallback_when_no_llm(self) -> None:
        """无LLM API key时返回兜底报告."""
        with patch("gold_miner.scenarios.analyzer.LLMClient") as mock_llm:
            mock_llm.return_value.enabled = False
            analyzer = ScenarioAnalyzer()
            report = analyzer.analyze(
                scenario_description="测试情景",
                time_horizon_months=6,
            )
            assert isinstance(report, ScenarioReport)
            assert report.scenario_description == "测试情景"
            assert report.time_horizon_months == 6
            assert "LLM未配置" in report.probability_assessment

    def test_parse_valid_llm_response(self) -> None:
        """解析有效的LLM JSON响应."""
        analyzer = ScenarioAnalyzer()
        mock_response = {
            "trigger_conditions": ["美债遭大规模抛售", "美元指数暴跌"],
            "transmission_channels": [
                {
                    "channel": "避险需求",
                    "direction": "bullish",
                    "magnitude": "strong",
                    "description": "避险资金涌入黄金",
                    "timeframe": "immediate",
                },
                {
                    "channel": "美元走弱",
                    "direction": "bullish",
                    "magnitude": "moderate",
                    "description": "美元与黄金负相关",
                    "timeframe": "medium-term",
                },
            ],
            "historical_analogs": [
                {
                    "event_name": "2011年美国信用降级",
                    "period": "2011",
                    "gold_price_change_pct": 30.0,
                    "similarity_score": 0.8,
                    "key_parallels": ["美元信用受损", "避险升温"],
                    "key_differences": ["当时处于QE时期"],
                },
            ],
            "price_impact": {
                "direction": "bullish",
                "base_case_change_pct": 25.0,
                "bullish_case_change_pct": 50.0,
                "bearish_case_change_pct": -10.0,
                "peak_impact_months": 8,
                "confidence": 0.7,
                "reasoning": "历史类比支撑看涨观点",
            },
            "key_levels": [2500, 3000, 3500],
            "probability_assessment": "中等概率事件，需关注美债拍卖需求",
            "strategy": {
                "overall_position": "增持",
                "spot_gold_action": "分批建仓",
                "accumulation_gold_action": "定投加码",
                "suggested_entry_zones": [2500, 2600],
                "suggested_exit_zones": [3500, 4000],
                "hedging_suggestions": ["配置部分美元现金", "做空美债"],
                "position_sizing": "不超过总资产30%",
                "rebalancing_frequency": "每月审视",
            },
            "risk_factors": ["美联储可能激进加息捍卫美元", "全球央行联合干预"],
            "monitoring_indicators": ["美债拍卖投标倍数", "外国持有美债数据"],
        }

        report = analyzer._parse_response(
            "test123",
            "美债危机",
            12,
            {"spot_gold": 2600.0},
            mock_response,
        )

        assert report.id == "test123"
        assert len(report.transmission_channels) == 2
        assert report.transmission_channels[0].channel == "避险需求"
        assert len(report.historical_analogs) == 1
        assert report.historical_analogs[0].gold_price_change_pct == 30.0
        assert report.price_impact is not None
        assert report.price_impact.direction == "bullish"
        assert report.price_impact.base_case_change_pct == 25.0
        assert report.price_impact.bullish_case_change_pct == 50.0
        assert len(report.key_levels) == 3
        assert report.strategy is not None
        assert report.strategy.overall_position == "增持"
        assert len(report.strategy.suggested_entry_zones) == 2
        assert len(report.strategy.hedging_suggestions) == 2
        assert len(report.risk_factors) == 2
        assert len(report.monitoring_indicators) == 2

    def test_parse_response_with_minimal_data(self) -> None:
        """解析最小化的LLM响应."""
        analyzer = ScenarioAnalyzer()
        report = analyzer._parse_response(
            "minimal",
            "简单情景",
            6,
            {},
            {
                "trigger_conditions": [],
                "transmission_channels": [],
                "historical_analogs": [],
                "price_impact": {
                    "direction": "neutral",
                    "base_case_change_pct": 0,
                    "bullish_case_change_pct": 0,
                    "bearish_case_change_pct": 0,
                    "peak_impact_months": 0,
                    "confidence": 0.5,
                    "reasoning": "",
                },
                "key_levels": [],
                "probability_assessment": "",
                "strategy": {
                    "overall_position": "观望",
                    "spot_gold_action": "",
                    "accumulation_gold_action": "",
                    "suggested_entry_zones": [],
                    "suggested_exit_zones": [],
                    "hedging_suggestions": [],
                    "position_sizing": "",
                    "rebalancing_frequency": "",
                },
                "risk_factors": [],
                "monitoring_indicators": [],
            },
        )
        assert report.id == "minimal"
        assert report.price_impact is not None
        assert report.price_impact.direction == "neutral"
        assert report.strategy is not None
        assert report.strategy.overall_position == "观望"


class TestScenarioStore:
    def test_save_and_load(self) -> None:
        """保存并加载情景报告."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ScenarioStore(data_dir=tmpdir)
            report = ScenarioReport(
                id="save_test",
                scenario_description="测试保存",
                time_horizon_months=6,
                price_impact=PriceImpactEstimate(
                    direction="bullish",
                    base_case_change_pct=10.0,
                    confidence=0.6,
                ),
            )
            store.save(report)

            loaded = store.load("save_test")
            assert loaded is not None
            assert loaded.id == "save_test"
            assert loaded.scenario_description == "测试保存"
            assert loaded.price_impact is not None
            assert loaded.price_impact.direction == "bullish"

    def test_load_nonexistent(self) -> None:
        """加载不存在的报告返回None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ScenarioStore(data_dir=tmpdir)
            assert store.load("nonexistent") is None

    def test_list_all(self) -> None:
        """列出所有报告."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ScenarioStore(data_dir=tmpdir)
            for i in range(3):
                report = ScenarioReport(
                    id=f"test_{i}",
                    scenario_description=f"情景{i}",
                    price_impact=PriceImpactEstimate(
                        direction="bullish",
                        base_case_change_pct=float(i * 10),
                        confidence=0.5 + i * 0.1,
                    ),
                )
                store.save(report)

            reports = store.list_all(limit=10)
            assert len(reports) == 3
            assert all(isinstance(r, ScenarioReport) for r in reports)

    def test_list_all_empty(self) -> None:
        """空存储列表."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ScenarioStore(data_dir=tmpdir)
            assert store.list_all() == []

    def test_save_with_strategy(self) -> None:
        """保存含完整策略的报告."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ScenarioStore(data_dir=tmpdir)
            report = ScenarioReport(
                id="strategy_test",
                scenario_description="测试策略保存",
                strategy=StrategyRecommendation(
                    overall_position="增持",
                    spot_gold_action="做多",
                    suggested_entry_zones=[2500.0, 2550.0],
                    suggested_exit_zones=[3000.0],
                    hedging_suggestions=["买入美元看跌期权"],
                    position_sizing="不超过总资产25%",
                    rebalancing_frequency="每周审视",
                ),
            )
            store.save(report)

            loaded = store.load("strategy_test")
            assert loaded is not None
            assert loaded.strategy is not None
            assert loaded.strategy.overall_position == "增持"
            assert loaded.strategy.suggested_entry_zones == [2500.0, 2550.0]
            assert loaded.strategy.hedging_suggestions == ["买入美元看跌期权"]
