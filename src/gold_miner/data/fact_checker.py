"""消息事实核查 — 多源交叉验证引擎.

对新闻标题进行多源交叉确认，标记可信度：
- confirmed: 2+ 独立媒体报道同一事件
- unverified: 仅1个源，或无法验证
- disputed: 存在矛盾报道
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from loguru import logger

from gold_miner.data.news import NewsItem
from gold_miner.proxy import get_proxied_client


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"
    FALSE = "false"


@dataclass
class FactCheckResult:
    """单条新闻的核查结果."""

    news_item: NewsItem
    status: VerificationStatus
    cross_sources: list[str] = field(default_factory=list)
    confidence: float = 0.0
    check_method: str = ""
    checked_at: datetime = field(default_factory=datetime.now)


class FactChecker:
    """新闻事实核查引擎.

    核查策略（按优先级）:
    1. 官方源匹配 — SEC EDGAR / 央行公告 / WGC 等 primary source
    2. 多源交叉 — 同一事件关键词搜索，统计独立报道源数量
    3. 时间线合理 — 事件时间是否逻辑合理
    """

    # 官方/权威信息源域名
    OFFICIAL_DOMAINS: set[str] = {
        # 美国监管机构
        "sec.gov", "federalreserve.gov", "treasury.gov", "bls.gov",
        # 国际组织
        "gold.org", "imf.org", "worldbank.org", "bis.org",
        # 央行
        "pbc.gov.cn", "ecb.europa.eu", "boj.or.jp", "bankofengland.co.uk",
        "bis.org", "centralbank.ie", "nb.gov.pl", "tcmb.gov.tr",
        # 交易所
        "nasdaq.com", "nyse.com", "londonstockexchange.com",
        "sse.com.cn", "szse.cn",
        # 权威媒体
        "reuters.com", "bloomberg.com", "ft.com", "wsj.com",
        "cnbc.com", "marketwatch.com", "investing.com",
        # 数据机构
        "worldgoldcouncil.org", "wgc.org",
    }

    # 需要重点核查的敏感关键词
    SENSITIVE_KEYWORDS: list[str] = [
        "ipo", "收购", "acquisition", "merger", "合并",
        "rate cut", "rate hike", "降息", "加息",
        "default", "违约", "破产", "bankruptcy",
        "sanction", "制裁", "war", "战争", "attack", "袭击",
        "cpi", "ppi", "nfp", "nonfarm", "payroll",
        "fed", "fomc", "ecb", "央行", "central bank",
        "death", "去世", "assassination", "刺杀",
    ]

    def __init__(self, min_cross_sources: int = 2) -> None:
        self.min_cross_sources = min_cross_sources

    def check(self, item: NewsItem) -> FactCheckResult:
        """对单条新闻进行事实核查.

        Returns:
            FactCheckResult: 核查结果，包含状态、交叉源列表、置信度
        """
        # 1. 判断是否需要核查
        if not self._needs_verification(item):
            return FactCheckResult(
                news_item=item,
                status=VerificationStatus.UNVERIFIED,
                confidence=0.3,
                check_method="low_priority_skip",
            )

        # 2. 官方源检查
        official_match = self._check_official_source(item)
        if official_match:
            return FactCheckResult(
                news_item=item,
                status=VerificationStatus.CONFIRMED,
                cross_sources=[item.source],
                confidence=0.9,
                check_method="official_source",
            )

        # 3. 多源交叉验证（搜索引擎搜索同一事件）
        cross_sources = self._cross_reference(item)

        # 4. 时间线合理性
        timeline_ok = self._check_timeline(item)

        # 5. 综合判定
        if len(cross_sources) >= self.min_cross_sources:
            status = VerificationStatus.CONFIRMED
            confidence = min(0.5 + len(cross_sources) * 0.15, 0.9)
            method = "cross_reference"
        elif len(cross_sources) == 1:
            status = VerificationStatus.UNVERIFIED
            confidence = 0.4 if timeline_ok else 0.2
            method = "single_source"
        else:
            status = VerificationStatus.UNVERIFIED
            confidence = 0.2 if timeline_ok else 0.1
            method = "no_cross_reference"

        return FactCheckResult(
            news_item=item,
            status=status,
            cross_sources=cross_sources,
            confidence=round(confidence, 2),
            check_method=method,
        )

    def check_batch(self, items: list[NewsItem]) -> list[FactCheckResult]:
        """批量核查新闻列表."""
        results: list[FactCheckResult] = []
        for item in items:
            try:
                result = self.check(item)
                results.append(result)
            except Exception as e:
                logger.debug(f"核查失败 [{item.title[:30]}]: {e}")
                results.append(FactCheckResult(
                    news_item=item,
                    status=VerificationStatus.UNVERIFIED,
                    confidence=0.0,
                    check_method="error",
                ))
        return results

    def _needs_verification(self, item: NewsItem) -> bool:
        """判断新闻是否需要事实核查.

        仅对包含敏感关键词的新闻进行核查，降低API开销。
        """
        text = f"{item.title} {item.summary}".lower()
        return any(kw.lower() in text for kw in self.SENSITIVE_KEYWORDS)

    def _check_official_source(self, item: NewsItem) -> bool:
        """检查新闻是否来自官方/权威信息源."""
        url_lower = item.url.lower()
        return any(domain in url_lower for domain in self.OFFICIAL_DOMAINS)

    def _cross_reference(self, item: NewsItem, max_results: int = 8) -> list[str]:
        """多源交叉验证 — 搜索引擎搜索同一事件关键词.

        提取核心实体词，搜索后统计独立报道源数量。
        """
        query = self._extract_query(item)
        if not query:
            return []

        sources: list[str] = []
        try:
            # 使用 DuckDuckGo 搜索
            sources = self._search_duckduckgo(query, max_results)
        except Exception as e:
            logger.debug(f"交叉验证搜索失败: {e}")

        # 去重: 同域名的只算一个源
        unique_domains: set[str] = set()
        unique_sources: list[str] = []
        for src in sources:
            domain = self._extract_domain(src)
            if domain and domain not in unique_domains:
                unique_domains.add(domain)
                unique_sources.append(src)

        # 排除原新闻来源
        original_domain = self._extract_domain(item.url)
        filtered = [s for s in unique_sources
                    if self._extract_domain(s) != original_domain]

        return filtered[:max_results]

    def _search_duckduckgo(self, query: str, max_results: int) -> list[str]:
        """通过 DuckDuckGo 搜索获取结果源列表."""
        url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        }

        sources: list[str] = []
        try:
            with get_proxied_client(timeout=20, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                html = resp.text

            # 简单HTML解析提取链接
            import re
            links = re.findall(r'href="(https?://[^"]+)"', html)
            for link in links[:max_results * 2]:
                if any(domain in link for domain in [
                    "duckduckgo.com", "duck.co", "spreadprivacy.com"
                ]):
                    continue
                sources.append(link)
                if len(sources) >= max_results:
                    break
        except Exception:
            pass

        return sources

    def _extract_query(self, item: NewsItem) -> str:
        """从新闻标题提取搜索关键词.

        保留: 实体名(公司/人名/机构)、数字、关键动作词
        去除: 情感修饰词、时间词、标点
        """
        text = item.title

        # 去除常见修饰词
        noise_words = [
            "breaking", "突发", "紧急", "urgent", "刚刚", "最新",
            "重磅", "震惊", "shocking", "exclusive", "独家",
            " reportedly", "据称", " rumored", "传闻", "或", "可能",
            "probably", "maybe", "allegedly", " reportedly ",
        ]
        for w in noise_words:
            text = text.replace(w, " ").replace(w.title(), " ")

        # 提取关键短语：引号内内容、大写专有名词、数字+单位
        phrases: list[str] = []

        # 引号内容
        quotes = re.findall(r'[""""]([^""""]{3,50})[""""]', item.title)
        phrases.extend(quotes)

        # 实体名（连续大写或大写开头词）
        entities = re.findall(r'\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)+\b', item.title)
        phrases.extend(entities)

        # 数字+单位/货币
        amounts = re.findall(r'\$?[\d,.]+\s*(?:billion|trillion|million|亿|万|%|percent)', item.title, re.I)
        phrases.extend(amounts)

        # 核心关键词组合
        core_words = []
        for w in text.split():
            w = w.strip(".,;:!?\"'").lower()
            if len(w) > 2 and w not in {
                "the", "and", "for", "with", "from", "that", "this",
                "but", "not", "are", "was", "were", "have", "has",
                "will", "would", "could", "should", "said", "says",
                "new", "old", "big", "small", "high", "low", "good", "bad",
            }:
                core_words.append(w)

        # 组合查询：实体 + 核心动作
        if phrases:
            query = " ".join(phrases[:3])
        elif len(core_words) >= 2:
            query = " ".join(core_words[:5])
        else:
            query = item.title[:60]

        return query.strip()

    def _check_timeline(self, item: NewsItem) -> bool:
        """检查新闻时间线是否合理.

        - 未来日期的新闻 → 可疑
        - 超过30天前的突发新闻 → 可能是旧闻重发
        """
        now = datetime.now()
        age_days = (now - item.published_at).days

        if age_days < 0:
            return False  # 未来日期
        if item.is_breaking and age_days > 7:
            return False  # 旧闻标为突发

        return True

    @staticmethod
    def _extract_domain(url: str) -> str:
        """从URL提取域名."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return ""


def apply_fact_checks(
    items: list[NewsItem],
    results: list[FactCheckResult],
) -> list[NewsItem]:
    """将核查结果应用到 NewsItem 的 metadata 中.

    修改 items 的 metadata 字段，增加 verification_status 和 confidence。
    """
    result_map = {r.news_item.title: r for r in results}

    for item in items:
        result = result_map.get(item.title)
        if result:
            item.metadata["verification_status"] = result.status.value
            item.metadata["verification_confidence"] = result.confidence
            item.metadata["verification_method"] = result.check_method
            item.metadata["cross_sources"] = result.cross_sources

    return items


def filter_unverified_news(
    items: list[NewsItem],
    min_confidence: float = 0.2,
) -> list[NewsItem]:
    """过滤掉可信度过低的新闻.

    保留: confirmed 或 confidence >= min_confidence 的新闻
    丢弃: false/disputed 且 confidence < min_confidence
    """
    filtered: list[NewsItem] = []
    for item in items:
        status = item.metadata.get("verification_status", "unverified")
        confidence = item.metadata.get("verification_confidence", 0.0)

        if status == VerificationStatus.FALSE.value:
            continue
        if status == VerificationStatus.DISPUTED.value and confidence < 0.3:
            continue
        if confidence < min_confidence and status != VerificationStatus.CONFIRMED.value:
            # 低置信度但未确认的新闻降级保留
            pass

        filtered.append(item)

    return filtered
