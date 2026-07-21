"""消息面信号 — 事件检测+NLP摘要."""
from __future__ import annotations

import math
import re
from collections import Counter

from gold_miner.data.fact_checker import FactChecker, apply_fact_checks, format_verification_tag
from gold_miner.data.news import NewsFetcher, NewsItem
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

# 地缘风险关键词 — 用于识别传统事实核查难以覆盖的突发性地缘事件
# 使用 tuple 保证不可变；使用词边界匹配避免 "war" 匹配 "forward" 等误触发
_GEOPOLITICAL_TRIGGERS: dict[str, tuple[str, ...]] = {
    "primary": (
        r"\biran\b", r"\bisrael\b", r"\bgaza\b", r"\bpalestine\b",
        r"\bhamas\b", r"\bhezbollah\b", r"\bhouthi\b", r"\byemen\b",
        r"\bukraine\b", r"\brussia\b", r"\bputin\b", r"\btaiwan\b",
        r"\bchina\b", r"\bnorth korea\b", r"\bhormuz\b",
        r"\bstrait of hormuz\b", r"\bmiddle east\b", r"\bgulf\b",
        r"\bsaudi\b", r"\bopec\b", r"\bgeopolitical\b",
        r"\bwar\b", r"\bconflict\b", r"\bmilitary attack\b",
        r"\bairstrike\b", r"\bceasefire\b", r"\bnegotiation\b",
    ),
    "oil_link": (
        r"\boil\b", r"\bcrude\b", r"\bpetroleum\b", r"\bopec\b",
        r"\bhormuz\b", r"\bshipping\b",
    ),
    "safe_haven_link": (
        r"\bgold\b", r"\bsafe haven\b", r"\bsafe-haven\b",
        r"\btreasury\b", r"\bdollar\b",
    ),
    # 印度黄金需求关键词 (2026-07-22) — WGC 年中报告列为下半年金价变量
    "india_gold": (
        r"\bindia gold\b", r"\bindian gold\b", r"\bindia import tariff\b",
        r"\bindia gold duty\b", r"\bindia gold demand\b",
        r"\brupee gold\b", r"\bindia gold smuggling\b",
        r"\bindian wedding season gold\b",
    ),
}


def _news_text(news: NewsItem) -> str:
    """返回用于关键词匹配的新闻文本."""
    return f"{news.title} {news.summary}".lower()


def _text_contains(text: str, patterns: tuple[str, ...]) -> bool:
    """使用词边界正则匹配一组关键词."""
    return any(re.search(pattern, text) for pattern in patterns)


def _is_geopolitical(news: NewsItem) -> bool:
    """判断新闻是否涉及地缘风险."""
    text = _news_text(news)
    return _text_contains(text, _GEOPOLITICAL_TRIGGERS["primary"])


def _geopolitical_boost(news: NewsItem) -> float:
    """根据地缘相关性和油价/避险联动性计算加分.

    Returns:
        0.0 ~ 0.4 的额外得分加成。
        纯地缘新闻 +0.2；同时涉及油价/霍尔木兹 +0.1；同时涉及黄金/避险 +0.1。
    """
    if not _is_geopolitical(news):
        return 0.0

    text = _news_text(news)
    boost = 0.2

    # 与油价/供应中断直接相关 → 通胀预期/加息概率传导更强
    if _text_contains(text, _GEOPOLITICAL_TRIGGERS["oil_link"]):
        boost += 0.1

    # 与黄金/避险资产直接相关
    if _text_contains(text, _GEOPOLITICAL_TRIGGERS["safe_haven_link"]):
        boost += 0.1

    return min(boost, 0.4)


def _aggregate_tier_tag(pool: list[NewsItem]) -> str:
    """聚合信号的可信度标签.

    不拿单条新闻的 tier 代表整体，而是按池子里来源层级分布给出 mixed 标签。
    """
    if not pool:
        return "[unverified]"
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
            "hormuz", "strait of hormuz", "attack", "strike", "ceasefire",
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
                v_status = news.metadata.get("verification_status", "unverified")
                v_conf = news.metadata.get("verification_confidence", 0.0)
                v_tier = news.metadata.get("source_tier", "unknown")

                # 已确认新闻 → 信号更强; disputed → 大幅降权
                if v_status == "confirmed":
                    score_multiplier = 1.2
                elif v_status == "disputed":
                    score_multiplier = 0.4
                elif _is_geopolitical(news) and news.is_breaking:
                    # 突发性地缘新闻往往只有 1 家媒体先报，传统事实核查会系统性降权。
                    # 这里允许未确认但具备地缘相关性的突发新闻使用接近确认的乘数。
                    score_multiplier = 1.0
                else:
                    score_multiplier = 0.8

                # 地缘风险加分：把中性报道的潜在方向性体现出来
                geo_boost = _geopolitical_boost(news)
                base_score = news.sentiment if abs(news.sentiment) > 0.05 else 0.0
                adjusted_score = max(-1.0, min(1.0, base_score * score_multiplier + geo_boost))

                # 方向由最终 adjusted_score 决定，避免情感与加分方向矛盾
                if adjusted_score > 0.05:
                    direction = SignalDirection.BULLISH
                    strength = SignalStrength.STRONG if adjusted_score > 0.5 else SignalStrength.MODERATE
                elif adjusted_score < -0.05:
                    direction = SignalDirection.BEARISH
                    strength = SignalStrength.STRONG if adjusted_score < -0.5 else SignalStrength.MODERATE
                else:
                    direction = SignalDirection.NEUTRAL
                    strength = SignalStrength.WEAK

                tag = format_verification_tag(news)

                # 输出具体资讯：保留完整标题、摘要、来源与链接
                title_display = news.title.strip()
                summary_display = (news.summary or news.title).strip()
                desc_parts = [summary_display]
                if news.source:
                    desc_parts.append(f"来源: {news.source}")
                if news.url:
                    desc_parts.append(f"链接: {news.url}")
                description = " | ".join(desc_parts)

                signals.append(Signal(
                    name=f"重大事件{tag}: {title_display[:80]}{'...' if len(title_display) > 80 else ''}",
                    dimension="news",
                    direction=direction,
                    strength=strength,
                    score=round(adjusted_score, 2),
                    description=description[:250],
                    metadata={
                        "source": news.source,
                        "url": news.url,
                        "verification_status": v_status,
                        "verification_confidence": v_conf,
                        "source_tier": v_tier,
                        "geopolitical": _is_geopolitical(news),
                        "geopolitical_boost": round(geo_boost, 2),
                        "full_title": news.title,
                        "full_summary": news.summary,
                    },
                ))

        # 事实核查降级警告（如果大量新闻无法验证）
        if len(items) >= 5 and len(unverified) / len(items) > 0.6:
            # 统计主导层级
            tier_counts: dict[str, int] = {}
            for it in items:
                t = it.metadata.get("source_tier", "unknown")
                tier_counts[t] = tier_counts.get(t, 0) + 1
            dominant_tier = max(tier_counts.items(), key=lambda kv: kv[1])[0] if tier_counts else "unknown"

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

        # 汇总具体资讯列表，方便下游直接展示新闻标题/来源/情感
        if items:
            top_items = items[:5]
            news_lines = []
            for it in top_items:
                s = it.sentiment
                e = "+" if s > 0.1 else "-" if s < -0.1 else "o"
                line = f"[{e}] [{it.source}] {it.title}"
                news_lines.append(line)
            signals.append(Signal(
                name="最近新闻资讯",
                dimension="news",
                direction=SignalDirection.NEUTRAL,
                strength=SignalStrength.WEAK,
                score=0.0,
                description="; ".join(news_lines[:3]),
                metadata={
                    "news_list": [
                        {
                            "title": it.title,
                            "source": it.source,
                            "url": it.url,
                            "sentiment": it.sentiment,
                            "verification_status": it.metadata.get("verification_status", "unverified"),
                            "published_at": (
                                it.published_at.isoformat()
                                if it.published_at is not None and hasattr(it.published_at, "isoformat")
                                else ""
                            ),
                        }
                        for it in top_items
                    ],
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

        # === 地缘风险溢价信号 ===
        # 传统 NLP 情感分析对中性报道的地缘新闻会给出接近 0 的分数，导致重大事件被忽略。
        # 这里把池子中所有地缘新闻聚合起来，单独生成一个风险溢价信号。
        geo_items = [it for it in items if _is_geopolitical(it)]
        if geo_items:
            total_geo_boost = sum(_geopolitical_boost(it) for it in geo_items)
            # 单条最高 0.4；多条按 sqrt 衰减聚合，避免重复计数
            aggregate_boost = min(0.6, total_geo_boost / math.sqrt(len(geo_items)))

            # 判断方向：默认地缘风险利好黄金；若明确谈判取得突破/停火延期则偏空
            text_all = " ".join(_news_text(it) for it in geo_items)
            de_escalation_patterns = (
                r"\bpeace deal\b", r"\breach a deal\b", r"\bdeal reached\b",
                r"\bpeace agreement\b", r"\bceasefire extended\b",
                r"\bceasefire deal\b", r"\btalks make progress\b",
                r"\bdiplomatic breakthrough\b",
            )
            escalation_patterns = (
                r"\battack\b", r"\bstrike\b", r"\bwar\b", r"\binvasion\b",
                r"\btension\b", r"\bthreat\b", r"\bhormuz\b",
                r"\bmilitary action\b", r"\bconflict escalat",
            )
            de_count = sum(1 for p in de_escalation_patterns if re.search(p, text_all))
            es_count = sum(1 for p in escalation_patterns if re.search(p, text_all))

            # 只有当缓和信号明显多于升级信号时才判定为 bearish，避免单一词汇误导
            if de_count >= 2 and de_count > es_count:
                geo_direction = SignalDirection.BEARISH
                geo_description = "地缘谈判取得进展/停火延续，避险溢价下降"
            else:
                geo_direction = SignalDirection.BULLISH
                geo_description = "地缘风险升温，推升黄金避险溢价"

            # 聚合具体标题列表
            geo_titles = [it.title.strip()[:60] for it in geo_items]
            geo_titles_str = "；".join(f"{i+1}.{t}" for i, t in enumerate(geo_titles[:8]))

            signals.append(Signal(
                name="地缘风险溢价",
                dimension="news",
                direction=geo_direction,
                strength=SignalStrength.MODERATE if aggregate_boost > 0.3 else SignalStrength.WEAK,
                score=round(aggregate_boost if geo_direction == SignalDirection.BULLISH else -aggregate_boost, 2),
                description=f"{len(geo_items)}条地缘新闻聚合：{geo_description} | {geo_titles_str}",
                metadata={
                    "geo_news_count": len(geo_items),
                    "aggregate_boost": round(aggregate_boost, 2),
                    "escalation_keywords": es_count,
                    "de_escalation_keywords": de_count,
                    "geo_news_titles": [it.title for it in geo_items],
                },
            ))

        return signals

    def fetch_and_analyze(self, hours: int = 24) -> list[Signal]:
        """抓取并分析新闻 — 含事实核查."""
        items = self.fetcher.fetch_latest(hours=hours)
        items = self.fetcher.analyze_sentiment(items)
        return self.analyze(items)
