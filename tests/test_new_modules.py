"""新模块综合测试 — Source Truth / COT / 国际ETF / 央行月度."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from gold_miner.data.central_bank import (
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
from gold_miner.data.fiscal import FiscalDataFetcher
from gold_miner.data.news import NewsItem
from gold_miner.signals.base import SignalDirection, SignalStrength
from gold_miner.signals.cot_signal import CotSignalGenerator
from gold_miner.signals.etf_flow_signal import EtfFlowSignalGenerator
from gold_miner.signals.news_signal import (
    NewsSignalGenerator,
    _geopolitical_boost,
    _is_geopolitical,
    _news_text,
)

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
            published_at=datetime.now(tz=timezone.utc) + timedelta(days=1),
        )
        assert checker._check_timeline(item) is False

    def test_check_timeline_old_breaking(self):
        checker = FactChecker()
        item = NewsItem(
            title="Test",
            source="Reuters",
            published_at=datetime.now(tz=timezone.utc) - timedelta(days=10),
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

    def test_fetch_parses_cftc_csv(self):
        """模拟 CFTC CSV 响应，验证能解析出标准 GOLD 持仓."""
        fetcher = CotReportFetcher()
        csv_text = (
            '"GOLD - COMMODITY EXCHANGE INC.",260616,2026-06-16,088691,CMX ,01,088 ,'
            '  339330,  211127,   30907,   26017,   58220,  265783,  295364,  322707,'
            '   43966,   16623,  339330,  211127,   30907,   26017,   58220,  265783,'
            '  295364,  322707,   43966,   16623\n'
            '"MICRO GOLD - COMMODITY EXCHANGE INC.",260616,2026-06-16,088695,CMX ,01,088 ,'
            '   57461,   16403,   39343,    2005,    7459,       0,   25867,   41348,'
            '   31594,   16113,   57461,   16403,   39343,    2005,    7459,       0,'
            '   25867,   41348,   31594,   16113\n'
        )

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = csv_text
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("gold_miner.data.cot_report.get_proxied_client") as mock_get_client:
            mock_get_client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_get_client.return_value.__exit__ = MagicMock(return_value=False)
            df = fetcher.fetch()

        assert not df.empty
        assert df.iloc[0]["timestamp"].date().isoformat() == "2026-06-16"
        assert df.iloc[0]["close"] == 180220.0  # 211127 - 30907
        assert df.iloc[0]["volume"] == 566037.0  # total OI
        assert df.iloc[0]["noncomm_ratio"] == pytest.approx(6.83, rel=0.01)


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
            result = fetcher.fetch_china_pboc()
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
        with (
            patch.object(gen.fetcher, "fetch_latest", return_value=items),
            patch.object(gen.fetcher, "analyze_sentiment", return_value=items),
            patch.object(gen.fact_checker, "check_batch", return_value=mock_results),
        ):
            signals = gen.fetch_and_analyze()
            # 至少应产生情感倾向信号
            assert any(s.dimension == "news" for s in signals)

    def test_geopolitical_news_generates_risk_premium_signal(self):
        """中性报道的地缘新闻应产生独立的看涨地缘风险溢价信号."""
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Iran war: Vance confirms US plan to expand forces in Middle East",
            source="Al Jazeera",
            published_at=datetime.now(),
            sentiment=0.0,
            is_breaking=True,
            metadata={"verification_status": "unverified"},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.UNVERIFIED,
            confidence=0.2, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])

        geo_signals = [s for s in signals if s.name == "地缘风险溢价"]
        assert len(geo_signals) == 1
        assert geo_signals[0].direction == SignalDirection.BULLISH
        assert geo_signals[0].score > 0.0
        assert "地缘风险升温" in geo_signals[0].description

    def test_geopolitical_oil_link_boosts_score(self):
        """涉及油价/霍尔木兹海峡的地缘新闻应获得更高加分."""
        iran_oil = NewsItem(
            title="Iran attacks force oil tankers to avoid Strait of Hormuz",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.0,
            is_breaking=True,
            metadata={"verification_status": "unverified"},
        )
        iran_only = NewsItem(
            title="Iran confirms diplomatic talks with US",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.0,
            is_breaking=True,
            metadata={"verification_status": "unverified"},
        )

        boost_oil = _geopolitical_boost(iran_oil)
        boost_only = _geopolitical_boost(iran_only)
        assert boost_oil > boost_only
        assert "hormuz" in _news_text(iran_oil)

    def test_geopolitical_de_escalation_turns_bearish(self):
        """地缘新闻明确显示缓和进展时，风险溢价信号应为看空."""
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="US and Iran reach peace deal and ceasefire deal, oil prices tumble",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.0,
            is_breaking=True,
            metadata={"verification_status": "confirmed"},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.CONFIRMED,
            confidence=0.8, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])

        geo_signals = [s for s in signals if s.name == "地缘风险溢价"]
        assert len(geo_signals) == 1
        assert geo_signals[0].direction == SignalDirection.BEARISH

    def test_non_geopolitical_words_not_false_positive(self):
        """普通词汇包含地缘子串时不应误判为地缘新闻."""
        warren = NewsItem(
            title="Warren Buffett sees no recession, forward guidance strong",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.3,
            metadata={"verification_status": "confirmed"},
        )
        assert not _is_geopolitical(warren)
        assert _geopolitical_boost(warren) == 0.0

    def test_geopolitical_boost_cap(self):
        """地缘新闻加分不超过 0.4."""
        item = NewsItem(
            title="Iran war threat closes Strait of Hormuz, gold safe haven demand surges",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.0,
            metadata={"verification_status": "confirmed"},
        )
        boost = _geopolitical_boost(item)
        assert boost == pytest.approx(0.4, abs=0.01)

    def test_geopolitical_direction_from_adjusted_score(self):
        """当情感偏空但地缘加分转正时，方向应与最终得分一致."""
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Iran war threat closes Strait of Hormuz, oil tankers halted",
            source="Al Jazeera",
            published_at=datetime.now(),
            sentiment=-0.1,
            is_breaking=True,
            metadata={"verification_status": "unverified"},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.UNVERIFIED,
            confidence=0.2, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])

        event_signals = [s for s in signals if "重大事件" in s.name]
        assert len(event_signals) >= 1
        # -0.1 * 1.0 + 0.3(geo: primary + oil_link + hormuz) = 0.2 > 0
        assert event_signals[0].direction == SignalDirection.BULLISH
        assert event_signals[0].score > 0.0

    def test_breaking_geopolitical_news_avoids_excessive_downweight(self):
        """突发性地缘新闻即使未确认，也不应被事实核查大幅降权."""
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Houthi attack closes Strait of Hormuz shipping lane",
            source="Breaking News Wire",
            published_at=datetime.now(),
            sentiment=0.0,
            is_breaking=True,
            metadata={"verification_status": "unverified"},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.UNVERIFIED,
            confidence=0.1, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])

        event_signals = [s for s in signals if "重大事件" in s.name]
        assert len(event_signals) >= 1
        # 突发性地缘新闻应使用 1.0 乘数 + 地缘加分，不应接近 0
        assert event_signals[0].score > 0.15
        assert event_signals[0].metadata.get("geopolitical") is True

    def test_non_geopolitical_news_unchanged(self):
        """非地缘新闻仍按原有规则打分."""
        gen = NewsSignalGenerator()
        item = NewsItem(
            title="Gold rises as US payrolls miss expectations",
            source="Reuters",
            published_at=datetime.now(),
            sentiment=0.5,
            is_breaking=True,
            metadata={"verification_status": "unverified"},
        )
        mock_result = FactCheckResult(
            news_item=item, status=VerificationStatus.UNVERIFIED,
            confidence=0.2, check_method="test",
        )
        with patch.object(gen.fact_checker, "check_batch", return_value=[mock_result]):
            signals = gen.analyze([item])

        event_signals = [s for s in signals if "重大事件" in s.name]
        assert len(event_signals) == 1
        # 原有规则：0.5 * 0.8 = 0.4
        assert event_signals[0].score == pytest.approx(0.4, abs=0.01)
        assert not event_signals[0].metadata.get("geopolitical")


# =============================================================================
# Fiscal Credit Tests
# =============================================================================

class TestFiscalDataFetcher:
    def test_fallback_data(self):
        fetcher = FiscalDataFetcher()
        df = fetcher._fallback_dataframe()
        assert not df.empty
        assert "timestamp" in df.columns
        assert "federal_debt_usd_billions" in df.columns
        assert df.iloc[-1]["source"] == "fallback"

    def test_fetch_uses_fred_when_key_configured(self):
        """模拟 FRED 响应，验证优先使用真实数据而非 fallback."""
        fetcher = FiscalDataFetcher()
        fetcher.api_key = "test_key"

        def _observations(series_id: str) -> dict[str, Any]:
            # 为不同 series 返回两条日期一致的观测值
            base = {
                "GFDEBTN": [
                    {"date": "2026-03-31", "value": "39500000"},  # 百万美元
                    {"date": "2026-06-30", "value": "40100000"},
                ],
                "GFDEGDQ188S": [
                    {"date": "2026-03-31", "value": "126.0"},
                    {"date": "2026-06-30", "value": "127.0"},
                ],
                "REAINTRATREARAT10Y": [
                    {"date": "2026-03-31", "value": "1.70"},
                    {"date": "2026-06-30", "value": "1.75"},
                ],
                "T10YIE": [
                    {"date": "2026-03-31", "value": "2.30"},
                    {"date": "2026-06-30", "value": "2.35"},
                ],
            }
            return {"observations": base.get(series_id, [])}

        def _fake_fallback_get(url, params=None, **kwargs):
            resp = MagicMock()
            resp.json.return_value = _observations(params.get("series_id", ""))
            return resp

        with patch("gold_miner.data.fiscal.fallback_get") as mock_fallback_get:
            mock_fallback_get.side_effect = _fake_fallback_get
            df = fetcher.fetch()

        assert not df.empty
        assert df.iloc[-1]["source"] == "FRED"
        # 联邦债务已从百万转换为十亿
        assert df.iloc[-1]["federal_debt_usd_billions"] == pytest.approx(40100.0)
        assert df.iloc[-1]["debt_to_gdp_pct"] == pytest.approx(127.0)
        assert df.iloc[-1]["real_rate_10y_pct"] == pytest.approx(1.75)
        assert df.iloc[-1]["breakeven_10y_pct"] == pytest.approx(2.35)

    def test_fetch_falls_back_without_api_key(self):
        fetcher = FiscalDataFetcher()
        fetcher.api_key = ""
        df = fetcher.fetch()
        assert not df.empty
        assert df.iloc[-1]["source"] == "fallback"
