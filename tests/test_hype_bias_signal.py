"""反带节奏信号测试."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from gold_miner.data.institutional_13f import InstitutionalSummary, InstitutionPosition
from gold_miner.data.investment_bank_targets import PriceTarget
from gold_miner.data.news import NewsItem
from gold_miner.signals.base import SignalDirection
from gold_miner.signals.hype_bias_signal import HypeBiasConfig, HypeBiasSignalGenerator
from gold_miner.storage.local import LocalFileStore


def _item(
    title: str = "",
    source: str = "test",
    sentiment: float = 0.0,
    tier: str = "T2",
) -> NewsItem:
    return NewsItem(
        title=title,
        source=source,
        published_at=datetime.now(),
        summary="",
        sentiment=sentiment,
        metadata={"source_tier": tier},
    )


class TestHypeBiasNewsSignals:
    """新闻面反带节奏检测测试."""

    def test_empty_and_small_pool_returns_no_signals(self):
        assert HypeBiasSignalGenerator(news_items=[]).generate_signals() == []
        assert HypeBiasSignalGenerator(news_items=[_item(sentiment=0.9)] * 4).generate_signals() == []

    def test_neutral_dominant_returns_no_signals(self):
        items = [_item(sentiment=0.0) for _ in range(5)]
        assert HypeBiasSignalGenerator(news_items=items).generate_signals() == []

    def test_clickbait_detected_and_direction_is_contrarian(self):
        items = [
            _item(title="金价暴涨！历史性突破", sentiment=0.9),
            _item(title="黄金 to the moon", sentiment=0.8),
            _item(title="多头狂欢", sentiment=0.7),
            _item(title="温和上涨", sentiment=0.3),
            _item(title="盘整", sentiment=0.1),
        ]
        gen = HypeBiasSignalGenerator(news_items=items)
        signals = gen.generate_signals()
        clickbait = [s for s in signals if s.metadata.get("heuristic") == "clickbait"]
        assert len(clickbait) == 1
        assert clickbait[0].direction == SignalDirection.BEARISH
        assert clickbait[0].score < 0

    def test_clickbait_not_triggered_below_threshold(self):
        items = [
            _item(title="金价小幅上涨", sentiment=0.6),
            _item(title="市场关注美联储", sentiment=0.5),
            _item(title="黄金走势分析", sentiment=0.4),
            _item(title="投资者观望", sentiment=0.3),
            _item(title="交投清淡", sentiment=0.2),
        ]
        signals = HypeBiasSignalGenerator(news_items=items).generate_signals()
        assert not any(s.metadata.get("heuristic") == "clickbait" for s in signals)

    def test_source_concentration_detected(self):
        items = [
            _item(source="LowCredMedia", sentiment=0.6),
            _item(source="LowCredMedia", sentiment=0.7),
            _item(source="LowCredMedia", sentiment=0.8),
            _item(source="LowCredMedia", sentiment=0.5),
            _item(source="OtherSource", sentiment=0.4),
        ]
        signals = HypeBiasSignalGenerator(news_items=items).generate_signals()
        concentration = [s for s in signals if s.metadata.get("heuristic") == "source_concentration"]
        assert len(concentration) == 1
        assert concentration[0].direction == SignalDirection.BEARISH
        assert concentration[0].metadata["top_source"] == "LowCredMedia"

    def test_source_concentration_not_triggered(self):
        items = [
            _item(source="A", sentiment=0.6),
            _item(source="A", sentiment=0.7),
            _item(source="B", sentiment=0.8),
            _item(source="C", sentiment=0.5),
            _item(source="D", sentiment=0.4),
        ]
        signals = HypeBiasSignalGenerator(news_items=items).generate_signals()
        assert not any(s.metadata.get("heuristic") == "source_concentration" for s in signals)

    def test_sentiment_extreme_bearish_is_bullish_contrarian(self):
        items = [
            _item(sentiment=-0.9),
            _item(sentiment=-0.8),
            _item(sentiment=-0.85),
            _item(sentiment=-0.75),
            _item(sentiment=-0.2),
        ]
        signals = HypeBiasSignalGenerator(news_items=items).generate_signals()
        extreme = [s for s in signals if s.metadata.get("heuristic") == "sentiment_extreme"]
        assert len(extreme) == 1
        assert extreme[0].direction == SignalDirection.BULLISH
        assert extreme[0].score > 0

    def test_low_tier_push_detected(self):
        items = [
            _item(source="blog1", sentiment=0.8, tier="T3"),
            _item(source="blog2", sentiment=0.7, tier="unknown"),
            _item(source="blog3", sentiment=0.9, tier="T3"),
            _item(source="Reuters", sentiment=0.3, tier="T2"),
            _item(source="Bloomberg", sentiment=0.2, tier="T1"),
        ]
        signals = HypeBiasSignalGenerator(news_items=items).generate_signals()
        low_tier = [s for s in signals if s.metadata.get("heuristic") == "low_tier_push"]
        assert len(low_tier) == 1
        assert low_tier[0].direction == SignalDirection.BEARISH

    def test_all_hype_bias_signals_are_contrarian(self):
        items = [
            _item(title="金价暴涨！历史性突破", source="blog1", sentiment=0.95, tier="T3"),
            _item(title="黄金 to the moon", source="blog1", sentiment=0.92, tier="T3"),
            _item(title="多头狂欢", source="blog2", sentiment=0.88, tier="unknown"),
            _item(title="突破在即", source="blog2", sentiment=0.85, tier="T3"),
            _item(title="大涨", source="blog3", sentiment=0.82, tier="T3"),
        ]
        signals = HypeBiasSignalGenerator(news_items=items).generate_signals()
        for s in signals:
            if s.metadata.get("heuristic") in {"clickbait", "source_concentration", "sentiment_extreme", "low_tier_push"}:
                assert s.direction == SignalDirection.BEARISH


class TestHypeBiasInstitutionalSignals:
    """机构面反带节奏检测测试."""

    def test_bank_target_divergence_detected(self):
        consensus = {
            "status": "ok",
            "total_banks": 7,
            "bullish_count": 6,
            "upside_pct": 10.0,
            "highest_target": 5000,
            "lowest_target": 3000,
            "avg_target": 4000,
        }
        with patch.object(
            HypeBiasSignalGenerator, "_bank_consensus_cached", return_value=consensus
        ):
            gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
            signals = gen.generate_signals()

        divergence = [s for s in signals if s.metadata.get("heuristic") == "bank_target_divergence"]
        assert len(divergence) == 1
        assert divergence[0].direction == SignalDirection.BEARISH
        assert divergence[0].score < 0

    def test_bank_target_divergence_not_triggered_when_consensus_low(self):
        consensus = {
            "status": "ok",
            "total_banks": 7,
            "bullish_count": 3,
            "upside_pct": -2.0,
            "highest_target": 3600,
            "lowest_target": 3000,
            "avg_target": 3300,
        }
        with patch.object(
            HypeBiasSignalGenerator, "_bank_consensus_cached", return_value=consensus
        ):
            gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
            signals = gen.generate_signals()

        assert not any(s.metadata.get("heuristic") == "bank_target_divergence" for s in signals)

    def test_said_bullish_sold_detected(self):
        consensus = {
            "status": "ok",
            "total_banks": 7,
            "bullish_count": 6,
            "upside_pct": 10.0,
        }
        summary = InstitutionalSummary(
            quarter="Q2 2026",
            total_institutions=5,
            net_gold_bullish=3,
            net_gold_bearish=2,
            top_sellers=[
                InstitutionPosition("Goldman Sachs", "GLD", 0, 0, "Q2 2026", -1.0, is_closed=True),
            ],
        )
        targets = [PriceTarget("Goldman Sachs", 3700, 3300, "Buy")]

        with (
            patch.object(HypeBiasSignalGenerator, "_bank_consensus_cached", return_value=consensus),
            patch.object(HypeBiasSignalGenerator, "_inst_summary_cached", return_value=summary),
            patch.object(HypeBiasSignalGenerator, "_bank_targets_cached", return_value=targets),
        ):
            gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
            signals = gen.generate_signals()

        sold = [s for s in signals if s.metadata.get("heuristic") == "said_bullish_sold"]
        assert len(sold) == 1
        assert sold[0].direction == SignalDirection.BEARISH
        assert sold[0].metadata["institution"] == "Goldman Sachs"

    def test_no_institutional_signals_without_spot(self):
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=0)
        assert gen.generate_signals() == []


class TestHypeBiasConfig:
    """配置与边界测试."""

    def test_custom_config_respected(self):
        config = HypeBiasConfig(min_news_pool=3, clickbait_ratio_threshold=0.2)
        items = [
            _item(title="暴涨！", sentiment=0.9),
            _item(title="突破", sentiment=0.8),
            _item(title="上涨", sentiment=0.7),
        ]
        gen = HypeBiasSignalGenerator(news_items=items, config=config)
        signals = gen.generate_signals()
        clickbait = [s for s in signals if s.metadata.get("heuristic") == "clickbait"]
        assert len(clickbait) == 1


class TestH7BankTargetFlipFlop:
    """H7 投行目标价反转测试."""

    def test_bearish_to_bullish_outputs_bearish_contrarian(self):
        history = [
            {
                "timestamp": (datetime.now() - timedelta(days=5)).isoformat(),
                "bank": "Goldman Sachs",
                "target_price": 3000,
                "current_spot": 3300,
                "upside_pct": -9.1,
            },
            {
                "timestamp": datetime.now().isoformat(),
                "bank": "Goldman Sachs",
                "target_price": 3700,
                "current_spot": 3300,
                "upside_pct": 12.1,
            },
        ]
        with patch.object(
            HypeBiasSignalGenerator, "_load_time_series_analyzer"
        ) as mock_load:
            from gold_miner.signals.institutional_time_series import (
                InstitutionalTimeSeriesAnalyzer,
            )

            mock_load.return_value = InstitutionalTimeSeriesAnalyzer(history)
            gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
            signals = gen._h7_bank_target_flip_flop()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BEARISH
        assert signals[0].metadata["heuristic"] == "bank_target_flip_flop"

    def test_bullish_to_bearish_outputs_bullish_contrarian(self):
        history = [
            {
                "timestamp": (datetime.now() - timedelta(days=5)).isoformat(),
                "bank": "Goldman Sachs",
                "target_price": 3700,
                "current_spot": 3300,
                "upside_pct": 12.1,
            },
            {
                "timestamp": datetime.now().isoformat(),
                "bank": "Goldman Sachs",
                "target_price": 3000,
                "current_spot": 3300,
                "upside_pct": -9.1,
            },
        ]
        with patch.object(
            HypeBiasSignalGenerator, "_load_time_series_analyzer"
        ) as mock_load:
            from gold_miner.signals.institutional_time_series import (
                InstitutionalTimeSeriesAnalyzer,
            )

            mock_load.return_value = InstitutionalTimeSeriesAnalyzer(history)
            gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
            signals = gen._h7_bank_target_flip_flop()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BULLISH

    def test_no_signal_without_spot(self):
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=0)
        assert gen._h7_bank_target_flip_flop() == []


class TestH8WalkTalkMismatch:
    """H8 言行不一测试."""

    def test_bullish_bank_with_selling_13f_outputs_bearish(self):
        consensus = {
            "status": "ok",
            "total_banks": 7,
            "bullish_count": 6,
            "upside_pct": 10.0,
        }
        targets = [PriceTarget("Goldman Sachs", 3700, 3300, "Buy")]
        history = [
            {
                "timestamp": datetime.now().isoformat(),
                "quarter": "Q2 2026",
                "institution": "Goldman Sachs Asset Management",
                "ticker": "GLD",
                "position_change_pct": -0.25,
            },
        ]

        with (
            patch.object(HypeBiasSignalGenerator, "_bank_consensus_cached", return_value=consensus),
            patch.object(HypeBiasSignalGenerator, "_bank_targets_cached", return_value=targets),
            patch.object(LocalFileStore, "load_institutional_13f_history", return_value=history),
        ):
            gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
            signals = gen._h8_walk_talk_mismatch()

        assert len(signals) == 1
        assert signals[0].direction == SignalDirection.BEARISH
        assert signals[0].metadata["heuristic"] == "walk_talk_mismatch"

    def test_no_mismatch_when_no_bullish_banks(self):
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
        with patch.object(HypeBiasSignalGenerator, "_bank_consensus_cached", return_value={"status": "no_data"}):
            assert gen._h8_walk_talk_mismatch() == []


class TestHypeBiasPersistence:
    """持久化测试."""

    def test_bank_targets_persisted(self):
        targets = [PriceTarget("Goldman Sachs", 3700, 3300, "Buy")]
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
        with patch.object(gen.store, "append_bank_target") as mock_append:
            gen._persist_bank_targets(targets)
            mock_append.assert_called_once()
            args = mock_append.call_args[0][0]
            assert args["bank"] == "Goldman Sachs"
            assert args["target_price"] == 3700

    def test_13f_holdings_persisted(self):
        summary = InstitutionalSummary(
            quarter="Q2 2026",
            total_institutions=5,
            net_gold_bullish=3,
            net_gold_bearish=2,
            top_sellers=[
                InstitutionPosition("Soros Fund", "GLD", 0, 0, "Q2 2026", -1.0, is_closed=True),
            ],
        )
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
        with patch.object(gen.store, "append_institutional_13f") as mock_append:
            gen._persist_13f_holdings(summary)
            mock_append.assert_called_once()
            args = mock_append.call_args[0][0]
            assert args["institution"] == "Soros Fund"
            assert args["ticker"] == "GLD"

    def test_is_recent_quarter(self):
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
        assert gen._is_recent_quarter("Q2 2026", 2) is True
        assert gen._is_recent_quarter("Q4 2025", 2) is False
        assert gen._is_recent_quarter("invalid", 2) is False

    def test_is_recent_quarter_future_ignored(self):
        gen = HypeBiasSignalGenerator(news_items=[], current_spot=3300)
        # 未来季度返回 False
        assert gen._is_recent_quarter("Q4 2099", 2) is False
