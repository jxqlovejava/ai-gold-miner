"""测试文章分析器."""

import pytest

from gold_miner.intelligence.analyzer import (
    ArticleAnalyzer,
    ArticleAnalysis,
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
)


BALANCED_ARTICLE = """
黄金短期面临回调压力，但中长期看涨。非农数据超预期，美元走强，
美联储降息预期降温。但全球央行购金趋势未变，地缘政治风险仍在。
金价在4200美元附近有较强支撑。建议逢低分批布局，控制仓位。
"""

SUSPICIOUS_ARTICLE = """
黄金即将迎来历史性暴涨！据知情人士透露，美联储将在6月降息50个基点，
毫无疑问这将推动金价突破5000美元。专家称美元将大幅贬值。
业内人士表示现在是最后机会，错过不再。加群跟单获取VIP策略。
"""


class TestArticleAnalyzer:
    def test_empty_text(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze("")
        assert result.word_count == 0
        assert result.sentiment_direction == "neutral"

    def test_balanced_article_sentiment(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze(BALANCED_ARTICLE)
        assert result.bullish_count > 0
        assert result.bearish_count > 0
        # 多空皆有 → 不应是极端方向
        assert abs(result.sentiment_score) < 0.8

    def test_suspicious_article_detected(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze(SUSPICIOUS_ARTICLE)
        assert result.is_suspicious is True
        assert result.manipulation_score >= 3

    def test_suspicious_article_bullish(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze(SUSPICIOUS_ARTICLE)
        assert result.sentiment_direction == "bullish"
        assert result.bullish_count > result.bearish_count

    def test_balanced_not_suspicious(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze(BALANCED_ARTICLE)
        assert result.is_suspicious is False
        assert result.manipulation_score < 3

    def test_claims_extracted(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze(SUSPICIOUS_ARTICLE)
        assert len(result.claims) >= 1
        assert any(c["category"] == "货币政策" for c in result.claims)

    def test_single_direction_detected(self):
        analyzer = ArticleAnalyzer()
        # 纯看涨文本
        text = "黄金暴涨 黄金飙升 黄金新高 央行购金 降息利好"
        result = analyzer.analyze(text)
        flags_text = " ".join(result.manipulation_flags)
        assert "单一方向" in flags_text

    def test_anonymous_source_detected(self):
        analyzer = ArticleAnalyzer()
        text = "据知情人士透露，金价将大涨。消息人士称央行将大量购金。"
        result = analyzer.analyze(text)
        flags_text = " ".join(result.manipulation_flags)
        assert "匿名来源" in flags_text

    def test_time_pressure_detected(self):
        analyzer = ArticleAnalyzer()
        text = "黄金即将暴涨，现在是最后机会，错过不再！"
        result = analyzer.analyze(text)
        flags_text = " ".join(result.manipulation_flags)
        assert "时间压力" in flags_text

    def test_promo_detected(self):
        analyzer = ArticleAnalyzer()
        text = "黄金将大涨，加群获取策略，扫码订阅VIP。"
        result = analyzer.analyze(text)
        flags_text = " ".join(result.manipulation_flags)
        assert "推销倾向" in flags_text

    def test_bearish_article(self):
        analyzer = ArticleAnalyzer()
        text = "黄金暴跌 美元走强 加息预期 资金流出 抛售压力 获利了结 经济复苏"
        result = analyzer.analyze(text)
        assert result.sentiment_direction == "bearish"
        assert result.bearish_count > result.bullish_count

    def test_summary_generated(self):
        analyzer = ArticleAnalyzer()
        result = analyzer.analyze(BALANCED_ARTICLE)
        assert len(result.summary) > 10
