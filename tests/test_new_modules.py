"""新模块综合测试 — Source Truth / COT / 国际ETF / 央行月度."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from gold_miner.data.central_bank import (
    CentralBankData,
    CentralBankFetcher,
    MonthlyCentralBankData,
    MonthlyCentralBankFetcher,
)
from gold_miner.data.cot_report import CotGoldData, CotReportFetcher
from gold_miner.data.etf_flow import IntlGoldEtfFlowFetcher
from gold_miner.data.fact_checker import (
    FactChecker,
    FactCheckResult,
    VerificationStatus,
    apply_fact_checks,
    filter_unverified_news,
)
from gold_miner.data.news import NewsItem
from gold_miner.signals.cot_signal import CotSignalGenerator
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.news_signal import NewsSignalGenerator
from gold_miner.signals.base import SignalDirection, SignalStrength


# =============================================================================
# Fact Checker Tests
# =============================================================================

class TestFactChecker:
    def test_needs_verification_sensitive_keyword(self):
        checker = FactChecker()
        item = NewsItem(
            title="SpaceX IPO plans $75 billion funding",
            source="Reuters",
            published_at=datetime.now(),
        )
        assert checker._needs_verification(item) is True

    def test_needs_verification_no_sensitive_keyword(self):
        checker = FactChecker()
        item = NewsItem(
            title="Gold price holds steady in Asian trade",
            source="Reuters",
            published_at=datetime.now(),
        )
        assert checker._needs_verification(item) is False

    def test_check_official_source(self):
        checker = FactChecker()
        item = NewsItem(
            title="Fed announces rate cut",
            source="Federal Reserve",
            published_at=datetime.now(),
            url="https://www.federalreserve.gov/news/2026/01/statement.htm",
        )
        result = checker.check(item)
        assert result.status == VerificationStatus.CONFIRMED
        assert result.check_method == "official_source"
        assert result.confidence == 0.9

    def test_check_unverified_single_source(self):
        checker = FactChecker()
        item = NewsItem(
            title="Breaking: Iran launches missile strike",
            source="Unknown Blog",
            published_at=datetime.now(),
            url="https://unknown-blog.com/news/123",
        )
        # Mock cross_reference to return empty
        with patch.object(checker, "_cross_reference", return_value=[]):
            result = checker.check(item)
            assert result.status == VerificationStatus.UNVERIFIED
            assert result.confidence < 0.5

    def test_extract_query_removes_noise(self):
        checker = FactChecker()
        item = NewsItem(
            title='Breaking: "SpaceX" targets $1.75 trillion IPO valuation',
            source="Reuters",
            published_at=datetime.now(),
        )
        query = checker._extract_query(item)
        assert "SpaceX" in query
        assert "trillion" in query or "1.75" in query
        assert "breaking" not in query.lower()

    def test_extract_domain(self):
        assert FactChecker._extract_domain("https://www.reuters.com/news/1") == "reuters.com"
        assert FactChecker._extract_domain("https://gold.org/research") == "gold.org"
        assert FactChecker._extract_domain("") == ""

    def test_apply_fact_checks(self):
        item = NewsItem(
            title="Test News",
            source="Reuters",
            published_at=datetime.now(),
        )
        result = FactCheckResult(
            news_item=item,
            status=VerificationStatus.CONFIRMED,
            confidence=0.85,
            check_method="cross_reference",
            cross_sources=["BBC", "CNBC"],
        )
        items = apply_fact_checks([item], [result])
        assert items[0].metadata["verification_status"] == "confirmed"
        assert items[0].metadata["verification_confidence"] == 0.85
        assert len(items[0].metadata["cross_sources"]) == 2

    def test_filter_unverified_news(self):
        item_confirmed = NewsItem(
            title="Confirmed",
            source="Reuters",
            published_at=datetime.now(),
            metadata={"verification_status": "confirmed", "verification_confidence": 0.9},
        )
        item_false = NewsItem(
            title="False",
            source="Fake",
            published_at=datetime.now(),
            metadata={"verification_status": "false", "verification_confidence": 0.1},
        )
        filtered = filter_unverified_news([item_confirmed, item_false])
        assert len(filtered) == 1
        assert filtered[0].title == "Confirmed"

    def test_check_timeline_future_date(self):
        checker = FactChecker()
        item = NewsItem(
            title="Test",
            source="Reuters",
            published_at=datetime.now() + timedelta(days=1),
        )
        assert checker._check_timeline(item) is False

    def test_check_timeline_old_breaking(self):
        checker = FactChecker()
        item = NewsItem(
            title="Test",
            source="Reuters",
            published_at=datetime.now() - timedelta(days=10),
            is_breaking=True,
        )
        assert checker._check_timeline(item) is False


# =============================================================================
# COT Report Tests
# =============================================================================

class TestCotGoldData:
    def test_properties(self):
        data = CotGoldData(
            report_date=datetime.now(),
            noncomm_long=200000,
            noncomm_short=50000,
            noncomm_spread=30000,
            comm_long=100000,
            comm_short=250000,
            nonrep_long=30000,
            nonrep_short=20000,
        )
        assert data.noncomm_net == 150000
        assert data.comm_net == -150000
        assert data.nonrep_net == 10000
        assert data.noncomm_ratio == 4.0
        assert data.total_oi == 600000


class TestCotReportFetcher:
    def test_fallback_data(self):
        fetcher = CotReportFetcher()
        df = fetcher._fallback_data()
        assert not df.empty
        assert "timestamp" in df.columns
        assert "close" in df.columns
        assert len(df) == 12

    def test_fetch_net_position_no_data(self):
        fetcher = CotReportFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame()):
            result = fetcher.fetch_net_position()
            assert result["status"] == "no_data"

    def test_fetch_net_position_ok(self):
        fetcher = CotReportFetcher()
        df = pd.DataFrame({
            "timestamp": [datetime.now() - timedelta(weeks=i) for i in range(4, -1, -1)],
            "open": [210000.0] * 5,
            "high": [210000.0] * 5,
            "low": [50000.0] * 5,
            "close": [150000.0, 155000.0, 160000.0, 165000.0, 170000.0],
            "volume": [500000.0] * 5,
            "comm_net": [-120000.0] * 5,
            "noncomm_ratio": [2.5] * 5,
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_net_position(weeks=4)
            assert result["status"] == "ok"
            assert result["latest_net"] == 170000
            assert result["prev_net"] == 165000
            assert result["change"] == 5000
            assert result["trend"] == "up"


class TestCotSignalGenerator:
    def test_trend_signal_up(self):
        gen = CotSignalGenerator()
        summary = {
            "status": "ok",
            "trend": "up",
            "change": 10000,
            "pct_change": 7.0,
            "latest_net": 170000,
        }
        with patch.object(gen.fetcher, "fetch_net_position", return_value=summary):
            signals = gen._trend_signals()
            assert len(signals) == 1
            assert "加仓" in signals[0].name
            assert signals[0].direction == SignalDirection.BULLISH
            assert signals[0].strength == SignalStrength.STRONG

    def test_trend_signal_down(self):
        gen = CotSignalGenerator()
        summary = {
            "status": "ok",
            "trend": "down",
            "change": -10000,
            "pct_change": -7.0,
            "latest_net": 150000,
        }
        with patch.object(gen.fetcher, "fetch_net_position", return_value=summary):
            signals = gen._trend_signals()
            assert len(signals) == 1
            assert "减仓" in signals[0].name
            assert signals[0].direction == SignalDirection.BEARISH

    def test_extreme_signal_crowded(self):
        gen = CotSignalGenerator()
        summary = {
            "status": "ok",
            "position_in_52w_range": 0.95,
            "latest_net": 280000,
        }
        with patch.object(gen.fetcher, "fetch_net_position", return_value=summary):
            signals = gen._extreme_signals()
            assert len(signals) == 1
            assert "拥挤" in signals[0].name
            assert signals[0].direction == SignalDirection.BEARISH

    def test_extreme_signal_pessimism(self):
        gen = CotSignalGenerator()
        summary = {
            "status": "ok",
            "position_in_52w_range": 0.05,
            "latest_net": 50000,
        }
        with patch.object(gen.fetcher, "fetch_net_position", return_value=summary):
            signals = gen._extreme_signals()
            assert len(signals) == 1
            assert "悲观" in signals[0].name
            assert signals[0].direction == SignalDirection.BULLISH

    def test_divergence_aligned_bullish(self):
        gen = CotSignalGenerator()
        df = pd.DataFrame({
            "timestamp": [datetime.now() - timedelta(weeks=1), datetime.now()],
            "close": [150000.0, 170000.0],
            "comm_net": [-150000.0, -130000.0],
        })
        with patch.object(gen.fetcher, "fetch", return_value=df):
            signals = gen._divergence_signals()
            assert len(signals) == 1
            assert "一致看多" in signals[0].name
            assert signals[0].direction == SignalDirection.BULLISH


# =============================================================================
# Intl Gold ETF Tests
# =============================================================================

class TestIntlGoldEtfFlowFetcher:
    def test_fetch_flow_summary_empty(self):
        fetcher = IntlGoldEtfFlowFetcher()
        with patch.object(fetcher, "fetch", return_value=pd.DataFrame()):
            result = fetcher.fetch_flow_summary()
            assert result["status"] == "no_data"

    def test_fetch_flow_summary_strong_inflow(self):
        fetcher = IntlGoldEtfFlowFetcher()
        df = pd.DataFrame({
            "symbol": ["GLD", "IAU", "GLDM", "PHYS", "SGOL"],
            "close": [200.0, 40.0, 50.0, 15.0, 25.0],
            "volume": [10000000, 5000000, 3000000, 1000000, 800000],
            "change_pct": [1.5, 1.2, 1.0, 0.8, 1.1],
            "volume_ratio": [2.0, 1.8, 1.6, 1.5, 1.7],
            "price_vs_ma20": [2.0, 1.5, 1.2, 0.8, 1.0],
            "open": [198.0, 39.5, 49.5, 14.8, 24.7],
            "high": [201.0, 40.5, 50.5, 15.2, 25.3],
            "low": [197.0, 39.0, 49.0, 14.5, 24.5],
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_flow_summary()
            assert result["status"] == "ok"
            assert result["flow_direction"] == "strong_inflow"
            assert result["flow_score"] > 0
            assert result["volume_surge_count"] >= 2

    def test_fetch_flow_summary_outflow(self):
        fetcher = IntlGoldEtfFlowFetcher()
        df = pd.DataFrame({
            "symbol": ["GLD", "IAU", "GLDM", "PHYS", "SGOL"],
            "close": [198.0, 39.0, 49.0, 14.5, 24.0],
            "volume": [10000000, 5000000, 3000000, 1000000, 800000],
            "change_pct": [-1.5, -1.2, -1.0, -0.8, -1.1],
            "volume_ratio": [2.0, 1.8, 1.6, 1.5, 1.7],
            "price_vs_ma20": [-2.0, -1.5, -1.2, -0.8, -1.0],
            "open": [200.0, 39.5, 49.5, 14.8, 24.5],
            "high": [200.5, 39.8, 49.8, 14.9, 24.8],
            "low": [197.0, 38.5, 48.5, 14.2, 23.8],
        })
        with patch.object(fetcher, "fetch", return_value=df):
            result = fetcher.fetch_flow_summary()
            assert result["status"] == "ok"
            assert result["flow_direction"] == "strong_outflow"
            assert result["flow_score"] < 0


class TestEtfFlowSignalGeneratorIntl:
    def test_intl_strong_inflow_signal(self):
        gen = EtfFlowSignalGenerator()
        intl_summary = {
            "status": "ok",
            "flow_direction": "strong_inflow",
            "flow_score": 0.8,
            "gld_change_pct": 1.5,
            "gld_volume_ratio": 2.2,
            "volume_surge_count": 3,
        }
        with patch.object(gen.intl_fetcher, "fetch_flow_summary", return_value=intl_summary):
            with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value={"status": "no_data"}):
                signals = gen._intl_gold_etf_signals()
                assert len(signals) >= 1
                assert any("国际黄金ETF大幅流入" in s.name for s in signals)
                assert any(s.direction == SignalDirection.BULLISH for s in signals)

    def test_intl_gld_volume_surge(self):
        gen = EtfFlowSignalGenerator()
        intl_summary = {
            "status": "ok",
            "flow_direction": "neutral",
            "flow_score": 0.0,
            "gld_change_pct": 0.8,
            "gld_volume_ratio": 2.5,
            "volume_surge_count": 1,
        }
        with patch.object(gen.intl_fetcher, "fetch_flow_summary", return_value=intl_summary):
            with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value={"status": "no_data"}):
                signals = gen._intl_gold_etf_signals()
                vol_signals = [s for s in signals if "GLD成交量异常放大" in s.name]
                assert len(vol_signals) == 1
                assert vol_signals[0].direction == SignalDirection.BULLISH

    def test_domestic_intl_divergence(self):
        gen = EtfFlowSignalGenerator()
        intl_summary = {
            "status": "ok",
            "flow_direction": "outflow",
            "flow_score": -0.5,
            "gld_change_pct": -1.0,
            "gld_volume_ratio": 1.2,
            "volume_surge_count": 0,
        }
        domestic = {
            "status": "ok",
            "flow_direction": "inflow",
            "avg_nav_change_pct": 1.0,
        }
        with patch.object(gen.intl_fetcher, "fetch_flow_summary", return_value=intl_summary):
            with patch.object(gen.gold_fetcher, "fetch_daily_change", return_value=domestic):
                signals = gen._intl_gold_etf_signals()
                div_signals = [s for s in signals if "内外盘背离" in s.name]
                assert len(div_signals) == 1
                assert div_signals[0].direction == SignalDirection.BULLISH


# =============================================================================
# Monthly Central Bank Tests
# =============================================================================

class TestMonthlyCentralBankData:
    def test_properties(self):
        data = MonthlyCentralBankData(
            country="中国",
            year=2026,
            month=6,
            net_purchases_tonnes=15.0,
            total_reserves_tonnes=2280.0,
        )
        assert data.date_label == "2026-06"
        assert data.is_significant is True

    def test_not_significant(self):
        data = MonthlyCentralBankData(
            country="新加坡",
            year=2026,
            month=6,
            net_purchases_tonnes=3.0,
        )
        assert data.is_significant is False


class TestMonthlyCentralBankFetcher:
    def test_fetch_summary(self):
        fetcher = MonthlyCentralBankFetcher()
        result = fetcher.fetch_summary()
        assert result["status"] == "ok"
        assert result["country_count"] == 5
        assert "total_monthly_tonnes" in result
        assert "trend" in result
        assert "top_buyer" in result
        assert len(result["details"]) == 5

    def test_fetch_china_pboc_fallback(self):
        fetcher = MonthlyCentralBankFetcher()
        # PBOC官网解析通常失败，测试回退
        with patch.object(fetcher.client, "get", side_effect=Exception("Connection error")):
            result = fetcher.fetch_china_pboC()
            assert result is not None
            assert result.country == "中国"
            assert result.net_purchases_tonnes > 0

    def test_fetch_all_countries(self):
        fetcher = MonthlyCentralBankFetcher()
        results = fetcher.fetch_all()
        assert len(results) == 5
        countries = [r.country for r in results]
        assert "中国" in countries
        assert "土耳其" in countries
        assert "波兰" in countries
        assert "印度" in countries
        assert "新加坡" in countries


# =============================================================================
# News Signal with Fact Check Tests
# =============================================================================

class TestNewsSignalGeneratorFactCheck:
    def test_confirmed_news_boosted_score(self):
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Gold surges as Fed signals rate cut",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.6,
            is_breaking=True,
            metadata={"verification_status": "confirmed", "verification_confidence": 0.9},
        )
        # Mock fact checker to preserve pre-set metadata
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.CONFIRMED,
            confidence=0.9, check_method="test", cross_sources=["BBC"],
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])
        event_signals = [s for s in signals if "重大事件" in s.name]
        assert len(event_signals) >= 1
        # 已确认新闻应有1.2倍乘数
        assert event_signals[0].score > 0.6

    def test_unverified_news_reduced_score(self):
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Gold surges as Fed signals rate cut",
            source="Unknown Blog",
            published_at=datetime.now(),
            sentiment=0.6,
            is_breaking=True,
            metadata={"verification_status": "unverified", "verification_confidence": 0.2},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.UNVERIFIED,
            confidence=0.2, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])
        event_signals = [s for s in signals if "重大事件" in s.name]
        assert len(event_signals) >= 1
        # 未确认新闻应有0.8倍乘数
        assert event_signals[0].score < 0.6

    def test_false_news_filtered_out(self):
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Fake gold crash news",
            source="Fake",
            published_at=datetime.now(),
            sentiment=-0.8,
            metadata={"verification_status": "false", "verification_confidence": 0.1},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.FALSE,
            confidence=0.1, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])
        # false新闻应被过滤，不产生重大事件信号
        event_signals = [s for s in signals if "重大事件" in s.name]
        assert len(event_signals) == 0

    def test_low_credibility_warning(self):
        gen = NewsSignalGenerator()
        items = [
            NewsItem(
                title=f"News {i}",
                source="Unknown",
                published_at=datetime.now(),
                sentiment=0.1,
                metadata={},
            )
            for i in range(6)
        ]
        mock_results = [
            FactCheckResult(
                news_item=it, status=VerificationStatus.UNVERIFIED,
                confidence=0.1, check_method="test",
            )
            for it in items
        ]
        with patch.object(gen.fact_checker, "check_batch", return_value=mock_results):
            signals = gen.analyze(items)
        warning = [s for s in signals if "可信度低" in s.name]
        assert len(warning) == 1

    def test_fetch_and_analyze_integration(self):
        gen = NewsSignalGenerator()
        items = [
            NewsItem(
                title="Gold prices rise on safe-haven demand",
                source="Reuters",
                published_at=datetime.now(),
                sentiment=0.3,
                metadata={},
            ),
            NewsItem(
                title="Dollar weakens as inflation data surprises",
                source="Bloomberg",
                published_at=datetime.now(),
                sentiment=0.2,
                metadata={},
            ),
            NewsItem(
                title="Central banks increase gold reserves",
                source="WGC",
                published_at=datetime.now(),
                sentiment=0.5,
                metadata={},
            ),
        ]
        mock_results = [
            FactCheckResult(
                news_item=it, status=VerificationStatus.CONFIRMED,
                confidence=0.8, check_method="test",
            )
            for it in items
        ]
        with patch.object(gen.fetcher, "fetch_latest", return_value=items):
            with patch.object(gen.fetcher, "analyze_sentiment", return_value=items):
                with patch.object(gen.fact_checker, "check_batch", return_value=mock_results):
                    signals = gen.fetch_and_analyze()
                    # 至少应产生情感倾向信号
                    assert any(s.dimension == "news" for s in signals)
