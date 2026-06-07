"""消息面信号 — 事件检测+NLP摘要."""

from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


class NewsSignalGenerator:
    """消息面信号生成器."""

    def __init__(self) -> None:
        self.fetcher = NewsFetcher()

    def analyze(self, items: list[NewsItem]) -> list[Signal]:
        """分析新闻列表，生成信号."""
        signals: list[Signal] = []

        if not items:
            return signals

        # 计算平均情感得分
        avg_sentiment = sum(n.sentiment for n in items) / len(items)
        bull_count = sum(1 for n in items if n.sentiment > 0)

        # 检测重大事件 (仅保留与黄金相关的)
        gold_impact_words = [
            "gold", "silver", "metal", "precious", "rate", "fed", "inflation",
            "payroll", "nfp", "nonfarm", "job", "cpi", "dollar", "treasury",
            "iran", "middle east", "israel", "war", "oil", "geopolitical",
            "central bank", "stimulus", "recession", "safe haven",
        ]
        breaking = [
            n for n in items
            if n.is_breaking and any(
                w in (n.title + " " + n.summary).lower()
                for w in gold_impact_words
            )
        ][:5]  # 最多5条重大事件

        if breaking:
            for news in breaking:
                direction = (
                    SignalDirection.BULLISH if news.sentiment > 0
                    else SignalDirection.BEARISH if news.sentiment < 0
                    else SignalDirection.NEUTRAL
                )
                strength = SignalStrength.STRONG if abs(news.sentiment) > 0.5 else SignalStrength.MODERATE
                signals.append(Signal(
                    name=f"重大事件: {news.title[:30]}...",
                    dimension="news",
                    direction=direction,
                    strength=strength,
                    score=news.sentiment,
                    description=news.summary[:100] if news.summary else news.title,
                    metadata={"source": news.source, "url": news.url},
                ))

        # 整体情感倾向（至少3条新闻，阈值 0.10）
        if len(items) >= 3 and abs(avg_sentiment) > 0.10:
            direction = (
                SignalDirection.BULLISH if avg_sentiment > 0
                else SignalDirection.BEARISH
            )
            strength = SignalStrength.MODERATE if abs(avg_sentiment) > 0.4 else SignalStrength.WEAK
            signals.append(Signal(
                name="新闻情感倾向",
                dimension="news",
                direction=direction,
                strength=strength,
                score=avg_sentiment,
                description=f"最近24h {len(items)}条新闻 平均情感 {avg_sentiment:+.2f}",
            ))

        # 新闻活跃度（≥5条相关新闻说明市场关注度高）
        if len(items) >= 5:
            bull_ratio = bull_count / len(items) if items else 0.5
            score = (bull_ratio - 0.5) * 0.4  # 最多 ±0.2
            signals.append(Signal(
                name="新闻活跃度",
                dimension="news",
                direction=SignalDirection.BULLISH if bull_ratio > 0.5 else SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=round(score, 2),
                description=f"24h内{len(items)}条相关新闻, 看涨占比{bull_ratio:.0%}",
            ))

        return signals

    def fetch_and_analyze(self, hours: int = 24) -> list[Signal]:
        """抓取并分析新闻."""
        items = self.fetcher.fetch_latest(hours=hours)
        items = self.fetcher.analyze_sentiment(items)
        return self.analyze(items)
