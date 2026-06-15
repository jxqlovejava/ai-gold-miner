"""测试新闻来源可信度与事实核查增强."""

from datetime import datetime

import pytest

from gold_miner.data.fact_checker import (
    FactChecker,
    FactCheckResult,
    VerificationStatus,
    format_verification_tag,
)
from gold_miner.data.news import NewsItem
from gold_miner.data.source_tiers import get_source_tier
from gold_miner.signals.news_signal import NewsSignalGenerator


class TestGetSourceTier:
    def test_t0_official_domains(self):
        assert get_source_tier("Federal Reserve", "https://federalreserve.gov/news") == "T0"
        assert get_source_tier("BLS", "https://bls.gov/data") == "T0"
        assert get_source_tier("SGE", "https://sge.com.cn") == "T0"
        assert get_source_tier("WGC", "https://worldgoldcouncil.org") == "T0"

    def test_t1_data_terminals(self):
        assert get_source_tier("Bloomberg", "https://bloomberg.com") == "T1"
        assert get_source_tier("Reuters", "https://reuters.com") == "T1"
        assert get_source_tier("WSJ", "https://wsj.com") == "T1"
        assert get_source_tier("CNBC", "https://cnbc.com") == "T1"

    def test_t2_reputable_media(self):
        assert get_source_tier("Caixin", "https://caixin.com") == "T2"
        assert get_source_tier("AP News", "https://apnews.com") == "T2"
        assert get_source_tier("The Guardian", "https://theguardian.com") == "T2"

    def test_t3_aggregators(self):
        assert get_source_tier("Bing", "https://bing.com") == "T3"
        assert get_source_tier("DuckDuckGo", "https://duckduckgo.com") == "T3"
        assert get_source_tier("Sina", "https://sina.com.cn") == "T3"
        assert get_source_tier("anysearch", "") == "T3"

    def test_t3_by_name_only(self):
        assert get_source_tier("anysearch", "") == "T3"
        assert get_source_tier("google", "") == "T3"
        assert get_source_tier("weibo", "") == "T3"

    def test_unknown(self):
        assert get_source_tier("SomeRandomBlog", "https://example.com") == "unknown"


class TestAnySearchQuotaExhausted:
    def test_quota_exhausted_returns_empty(self):
        from gold_miner.data.news import AnySearchFetcher

        fetcher = AnySearchFetcher()
        text = "Error: daily_free_quota_exhausted. Please try again tomorrow."
        items = fetcher._parse_anysearch_results(text)
        assert items == []

    def test_free_quota_exhausted_variant(self):
        from gold_miner.data.news import AnySearchFetcher

        fetcher = AnySearchFetcher()
        text = "Your free quota is exhausted. Upgrade for more searches."
        items = fetcher._parse_anysearch_results(text)
        assert items == []

    def test_api_error_returns_empty(self):
        from gold_miner.data.news import AnySearchFetcher

        fetcher = AnySearchFetcher()
        text = "API error: service unavailable"
        items = fetcher._parse_anysearch_results(text)
        assert items == []

    def test_normal_text_parsed(self):
        from gold_miner.data.news import AnySearchFetcher

        fetcher = AnySearchFetcher()
        text = "Gold prices surge amid Fed rate cut expectations"
        items = fetcher._parse_anysearch_results(text)
        assert len(items) == 1
        assert items[0].title == text


class TestFactCheckerConflictDetection:
    def test_disputed_when_conflict_detected(self):
        checker = FactChecker(min_cross_sources=2)

        item = NewsItem(
            title="Fed signals rate hike in June meeting",
            source="TestSource",
            published_at=datetime.now(),
            summary="",
        )

        # Mock _cross_reference to return sources that would trigger conflict
        # We test _detect_conflict directly
        cross_texts = ["Fed signals rate cut in June meeting"]
        assert checker._detect_conflict(item, cross_texts) is True

    def test_no_conflict_when_aligned(self):
        checker = FactChecker(min_cross_sources=2)

        item = NewsItem(
            title="Gold rises on safe haven demand",
            source="TestSource",
            published_at=datetime.now(),
            summary="",
        )
        cross_texts = ["Gold prices increase amid geopolitical tensions"]
        assert checker._detect_conflict(item, cross_texts) is False

    def test_disputed_status_via_check(self):
        checker = FactChecker(min_cross_sources=1)

        item = NewsItem(
            title="War breaks out in Middle East, talks stalled",
            source="TestSource",
            published_at=datetime.now(),
            summary="",
        )

        # Patch instance methods directly after __init__
        original_cross_ref = checker._cross_reference
        original_detect_conflict = checker._detect_conflict

        def mock_cross_ref(item, max_results=8):
            return ["https://example.com/news"]

        def mock_detect_conflict(item, cross_sources):
            return True

        checker._cross_reference = mock_cross_ref
        checker._detect_conflict = mock_detect_conflict

        try:
            result = checker.check(item)
            assert result.status == VerificationStatus.DISPUTED
            assert result.check_method == "conflict_detected"
        finally:
            checker._cross_reference = original_cross_ref
            checker._detect_conflict = original_detect_conflict


class TestNewsSignalMetadata:
    def test_signal_contains_source_tier_and_verification(self, monkeypatch):
        gen = NewsSignalGenerator()

        # Mock fact checker to preserve metadata
        def mock_check_batch(self, items):
            from gold_miner.data.fact_checker import VerificationStatus
            results = []
            for item in items:
                status_str = item.metadata.get("verification_status", "unverified")
                status = VerificationStatus(status_str)
                results.append(FactCheckResult(
                    news_item=item,
                    status=status,
                    confidence=item.metadata.get("verification_confidence", 0.5),
                    check_method="test_mock",
                ))
            return results

        monkeypatch.setattr(FactChecker, "check_batch", mock_check_batch)

        items = [
            NewsItem(
                title="Gold prices rise on Fed dovish stance",
                source="Reuters",
                published_at=datetime.now(),
                sentiment=0.5,
                is_breaking=True,
                summary="Gold up 1%",
                metadata={
                    "verification_status": "confirmed",
                    "source_tier": "T1",
                    "verification_confidence": 0.8,
                },
            ),
        ]

        signals = gen.analyze(items)
        breaking_signals = [s for s in signals if "重大事件" in s.name]
        assert len(breaking_signals) >= 1
        sig = breaking_signals[0]
        assert sig.metadata.get("source_tier") == "T1"
        assert sig.metadata.get("verification_status") == "confirmed"

    def test_disputed_signal_downweighted(self, monkeypatch):
        gen = NewsSignalGenerator()

        def mock_check_batch(self, items):
            from gold_miner.data.fact_checker import VerificationStatus
            results = []
            for item in items:
                status_str = item.metadata.get("verification_status", "unverified")
                status = VerificationStatus(status_str)
                results.append(FactCheckResult(
                    news_item=item,
                    status=status,
                    confidence=item.metadata.get("verification_confidence", 0.3),
                    check_method="test_mock",
                ))
            return results

        monkeypatch.setattr(FactChecker, "check_batch", mock_check_batch)

        items = [
            NewsItem(
                title="Gold crashes on unexpected news",
                source="RandomBlog",
                published_at=datetime.now(),
                sentiment=-0.6,
                is_breaking=True,
                summary="Gold down 2%",
                metadata={
                    "verification_status": "disputed",
                    "source_tier": "T3",
                    "verification_confidence": 0.3,
                },
            ),
        ]

        signals = gen.analyze(items)
        breaking_signals = [s for s in signals if "重大事件" in s.name]
        assert len(breaking_signals) >= 1
        sig = breaking_signals[0]
        # disputed uses multiplier 0.4 instead of 0.8
        expected_score = max(-1.0, min(1.0, -0.6 * 0.4))
        assert sig.score == pytest.approx(round(expected_score, 2))

    def test_verified_ratio_affects_sentiment_score(self, monkeypatch):
        gen = NewsSignalGenerator()

        def mock_check_batch(self, items):
            from gold_miner.data.fact_checker import VerificationStatus
            results = []
            for item in items:
                status_str = item.metadata.get("verification_status", "unverified")
                status = VerificationStatus(status_str)
                results.append(FactCheckResult(
                    news_item=item,
                    status=status,
                    confidence=0.5,
                    check_method="test_mock",
                ))
            return results

        monkeypatch.setattr(FactChecker, "check_batch", mock_check_batch)

        items = [
            NewsItem(
                title="Gold bullish outlook",
                source="Reuters",
                published_at=datetime.now(),
                sentiment=0.5,
                metadata={
                    "verification_status": "confirmed",
                    "source_tier": "T1",
                },
            ),
            NewsItem(
                title="Gold bullish outlook 2",
                source="Bloomberg",
                published_at=datetime.now(),
                sentiment=0.4,
                metadata={
                    "verification_status": "confirmed",
                    "source_tier": "T1",
                },
            ),
            NewsItem(
                title="Gold bullish outlook 3",
                source="Blog",
                published_at=datetime.now(),
                sentiment=0.3,
                metadata={
                    "verification_status": "unverified",
                    "source_tier": "unknown",
                },
            ),
        ]

        signals = gen.analyze(items)
        sentiment_signals = [s for s in signals if "新闻情感倾向" in s.name]
        assert len(sentiment_signals) >= 1
        sig = sentiment_signals[0]
        # verified_ratio = 2/3, multiplier = 0.5 + 0.5 * 0.667 = 0.833
        assert sig.metadata.get("verified_ratio") == pytest.approx(0.67, abs=0.01)


class TestFormatVerificationTag:
    def test_confirmed_with_tier(self):
        item = NewsItem(
            title="Test",
            source="Reuters",
            published_at=datetime.now(),
            metadata={"verification_status": "confirmed", "source_tier": "T1"},
        )
        assert format_verification_tag(item) == "[verified: T1]"

    def test_unverified(self):
        item = NewsItem(
            title="Test",
            source="Blog",
            published_at=datetime.now(),
            metadata={"verification_status": "unverified"},
        )
        assert format_verification_tag(item) == "[unverified]"

    def test_disputed(self):
        item = NewsItem(
            title="Test",
            source="Blog",
            published_at=datetime.now(),
            metadata={"verification_status": "disputed", "source_tier": "T3"},
        )
        assert format_verification_tag(item) == "[disputed]"

    def test_default_unverified(self):
        item = NewsItem(
            title="Test",
            source="Blog",
            published_at=datetime.now(),
            metadata={},
        )
        assert format_verification_tag(item) == "[unverified]"
