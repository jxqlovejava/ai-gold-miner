"""消息面信号 — 事件检测+NLP摘要."""

from gold_miner.data.fact_checker import FactChecker, apply_fact_checks, format_verification_tag
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength


def _aggregate_tier_tag(pool: list[NewsItem]) -> str:
    """聚合信号的可信度标签.

    不拿单条新闻的 tier 代表整体，而是按池子里来源层级分布给出 mixed 标签。
    """
    if not pool:
        return "[unverified]"
    from collections import Counter
    tiers = [it.metadata.get("source_tier", "unknown") for it in pool]
    counts = Counter(tiers)
    dominant, count = counts.most_common(1)[0]
    ratio = count / len(tiers)
    if dominant == "unknown" or ratio < 0.4:
        return "[mixed]"
    if ratio >= 0.6:
        return f"[mixed: {dominant}]"
    return "[mixed]"


class NewsSignalGenerator:
    """消息面信号生成器 — 集成事实核查."""

    def __init__(self) -> None:
        self.fetcher = NewsFetcher()
        self.fact_checker = FactChecker(min_cross_sources=2)

    def analyze(self, items: list[NewsItem]) -> list[Signal]:
        """分析新闻列表，生成信号 — 集成事实核查."""
        signals: list[Signal] = []

        if not items:
            return signals

        # === 事实核查 ===
        check_results = self.fact_checker.check_batch(items)
        items = apply_fact_checks(items, check_results)

        # 过滤掉可信度极低的新闻
        items = [it for it in items
                 if it.metadata.get("verification_status") != "false"]

        # 统计核查结果
        confirmed = [it for it in items
                     if it.metadata.get("verification_status") == "confirmed"]
        unverified = [it for it in items
                      if it.metadata.get("verification_status") == "unverified"]
        disputed = [it for it in items
                    if it.metadata.get("verification_status") == "disputed"]

        # 计算平均情感得分（优先使用已确认新闻）
        sentiment_pool = confirmed if len(confirmed) >= 3 else items
        if not sentiment_pool:
            return signals  # 过滤后无可用新闻
        avg_sentiment = sum(n.sentiment for n in sentiment_pool) / len(sentiment_pool)
        bull_count = sum(1 for n in sentiment_pool if n.sentiment > 0)

        # 检测重大事件 (仅保留与黄金相关的, 优先已确认)
        gold_impact_words = [
            "gold", "silver", "metal", "precious", "rate", "fed", "inflation",
            "payroll", "nfp", "nonfarm", "job", "cpi", "dollar", "treasury",
            "iran", "middle east", "israel", "war", "oil", "geopolitical",
            "central bank", "stimulus", "recession", "safe haven",
        ]

        # 先尝试已确认的重大新闻
        breaking_confirmed = [
            n for n in confirmed
            if n.is_breaking and any(
                w in (n.title + " " + n.summary).lower()
                for w in gold_impact_words
            )
        ]
        # 补充未确认但可能是重大的
        breaking_all = breaking_confirmed + [
            n for n in items
            if n.is_breaking and n not in breaking_confirmed and any(
                w in (n.title + " " + n.summary).lower()
                for w in gold_impact_words
            )
        ]
        breaking = breaking_all[:5]

        if breaking:
            for news in breaking:
                direction = (
                    SignalDirection.BULLISH if news.sentiment > 0
                    else SignalDirection.BEARISH if news.sentiment < 0
                    else SignalDirection.NEUTRAL
                )
                strength = SignalStrength.STRONG if abs(news.sentiment) > 0.5 else SignalStrength.MODERATE
                v_status = news.metadata.get("verification_status", "unverified")
                v_conf = news.metadata.get("verification_confidence", 0.0)
                v_tier = news.metadata.get("source_tier", "unknown")

                # 已确认新闻 → 信号更强; disputed → 大幅降权
                if v_status == "confirmed":
                    score_multiplier = 1.2
                elif v_status == "disputed":
                    score_multiplier = 0.4
                else:
                    score_multiplier = 0.8

                adjusted_score = max(-1.0, min(1.0, news.sentiment * score_multiplier))
                tag = format_verification_tag(news)

                signals.append(Signal(
                    name=f"重大事件{tag}: {news.title[:30]}...",
                    dimension="news",
                    direction=direction,
                    strength=strength,
                    score=round(adjusted_score, 2),
                    description=news.summary[:100] if news.summary else news.title,
                    metadata={
                        "source": news.source,
                        "url": news.url,
                        "verification_status": v_status,
                        "verification_confidence": v_conf,
                        "source_tier": v_tier,
                    },
                ))

        # 事实核查降级警告（如果大量新闻无法验证）
        if len(items) >= 5 and len(unverified) / len(items) > 0.6:
            # 统计主导层级
            tier_counts: dict[str, int] = {}
            for it in items:
                t = it.metadata.get("source_tier", "unknown")
                tier_counts[t] = tier_counts.get(t, 0) + 1
            dominant_tier = max(tier_counts, key=tier_counts.get) if tier_counts else "unknown"

            signals.append(Signal(
                name="新闻可信度低警告",
                dimension="news",
                direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.WEAK,
                score=0.0,
                description=f"{len(unverified)}/{len(items)}条新闻无法交叉验证，主导层级 {dominant_tier}，新闻面信号置信度下降",
                metadata={
                    "source": "fact_checker",
                    "unverified_ratio": len(unverified) / len(items),
                    "dominant_tier": dominant_tier,
                    "tier_breakdown": tier_counts,
                },
            ))

        # 整体情感倾向（至少3条新闻，阈值 0.10）
        if len(sentiment_pool) >= 3 and abs(avg_sentiment) > 0.10:
            direction = (
                SignalDirection.BULLISH if avg_sentiment > 0
                else SignalDirection.BEARISH
            )
            strength = SignalStrength.MODERATE if abs(avg_sentiment) > 0.4 else SignalStrength.WEAK
            # verified_ratio 加权: 已确认比例越高，信号越强
            verified_ratio = len(confirmed) / len(items) if items else 0
            score_multiplier = 0.5 + 0.5 * verified_ratio
            adjusted_score = avg_sentiment * score_multiplier

            signals.append(Signal(
                name=f"新闻情感倾向{_aggregate_tier_tag(sentiment_pool)}",
                dimension="news",
                direction=direction,
                strength=strength,
                score=round(adjusted_score, 2),
                description=f"最近24h {len(items)}条新闻({len(confirmed)}确认) 平均情感 {avg_sentiment:+.2f}",
                metadata={
                    "verified_ratio": round(verified_ratio, 2),
                    "confirmed_count": len(confirmed),
                    "unverified_count": len(unverified),
                    "disputed_count": len(disputed),
                    "aggregate_tier": _aggregate_tier_tag(sentiment_pool),
                },
            ))

        # 新闻活跃度（≥5条相关新闻说明市场关注度高）
        if len(sentiment_pool) >= 5:
            bull_ratio = bull_count / len(sentiment_pool) if sentiment_pool else 0.5
            score = (bull_ratio - 0.5) * 0.4  # 最多 ±0.2
            # 如果 confirmed 为 0 且 mostly unverified，降低幅度 50%
            if len(confirmed) == 0 and len(unverified) / len(items) > 0.6:
                score *= 0.5

            signals.append(Signal(
                name=f"新闻活跃度{_aggregate_tier_tag(sentiment_pool)}",
                dimension="news",
                direction=SignalDirection.BULLISH if bull_ratio > 0.5 else SignalDirection.BEARISH,
                strength=SignalStrength.WEAK,
                score=round(score, 2),
                description=f"24h内{len(items)}条相关新闻({len(confirmed)}确认), 看涨占比{bull_ratio:.0%}",
                metadata={
                    "confirmed_count": len(confirmed),
                    "unverified_count": len(unverified),
                    "aggregate_tier": _aggregate_tier_tag(sentiment_pool),
                },
            ))

        return signals

    def fetch_and_analyze(self, hours: int = 24) -> list[Signal]:
        """抓取并分析新闻 — 含事实核查."""
        items = self.fetcher.fetch_latest(hours=hours)
        items = self.fetcher.analyze_sentiment(items)
        return self.analyze(items)
