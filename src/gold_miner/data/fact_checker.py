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
from gold_miner.compat import StrEnum

from loguru import logger

from gold_miner.data.news import NewsItem
from gold_miner.data.source_tiers import _domain_matches, get_source_tier
from gold_miner.proxy import get_proxied_client


class VerificationStatus(StrEnum):
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
        "centralbank.ie", "nb.gov.pl", "tcmb.gov.tr",
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

    # 冲突检测关键词对 — 仅限明确的事件性矛盾，避免把同篇文章里的多空讨论判为冲突
    CONFLICT_PAIRS: list[tuple[str, str]] = [
        ("talks in muscat", "signing in geneva"),
        ("rate hike", "rate cut"),
        ("加息", "降息"),
        ("breakthrough", "stalled"),
        ("agreement reached", "talks collapsed"),
        ("deal signed", "deal rejected"),
        ("war", "peace deal signed"),
        ("军事打击", "取消军事打击"),
    ]

    def __init__(self, min_cross_sources: int = 2) -> None:
        self.min_cross_sources = min_cross_sources

    def check(self, item: NewsItem) -> FactCheckResult:
        """对单条新闻进行事实核查.

        Returns:
            FactCheckResult: 核查结果，包含状态、交叉源列表、置信度
        """
        # 确定 source_tier
        source_tier = item.metadata.get("source_tier")
        if not source_tier:
            source_tier = get_source_tier(item.source, item.url)

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

        # 3. 专项验证
        if "fomc" in item.title.lower() or "fed" in item.title.lower():
            fomc_result = self._verify_fomc_meeting(item)
            if fomc_result:
                return fomc_result

        if "au99.99" in item.title.lower() or "上金所" in item.title:
            sge_result = self._verify_sge_gold_price(item)
            if sge_result:
                return sge_result

        # 4. 多源交叉验证（搜索引擎搜索同一事件）
        cross_sources = self._cross_reference(item)

        # 5. 冲突检测
        if cross_sources and self._detect_conflict(item, cross_sources):
            return FactCheckResult(
                news_item=item,
                status=VerificationStatus.DISPUTED,
                cross_sources=cross_sources,
                confidence=0.3,
                check_method="conflict_detected",
            )

        # 6. 时间线合理性
        timeline_ok = self._check_timeline(item)

        # 7. 综合判定
        if len(cross_sources) >= self.min_cross_sources:
            status = VerificationStatus.CONFIRMED
            confidence = min(0.5 + len(cross_sources) * 0.15, 0.9)
            method = "cross_reference"
        elif len(cross_sources) == 1:
            status = VerificationStatus.UNVERIFIED
            confidence = 0.4 if timeline_ok else 0.2
            method = "single_source"
        else:
            # 无交叉引用时，基于 source_tier 做静态分级 fallback
            # 避免高质量源因 web search 不可用而被错误降级
            status = VerificationStatus.UNVERIFIED
            confidence = self._static_tier_confidence(source_tier, timeline_ok)
            method = f"static_tier_{source_tier}"

        return FactCheckResult(
            news_item=item,
            status=status,
            cross_sources=cross_sources,
            confidence=round(confidence, 2),
            check_method=method,
        )

    @staticmethod
    def _static_tier_confidence(source_tier: str, timeline_ok: bool) -> float:
        """基于 source_tier 的静态置信度 fallback.

        当 web search 不可用无法交叉验证时，依据源本身的可信度层级给基础分。
        """
        base = {
            "T0": 0.55,
            "T1": 0.40,
            "T2": 0.30,
            "T3": 0.15,
            "unknown": 0.10,
        }.get(source_tier, 0.10)
        if not timeline_ok:
            base *= 0.5
        return base

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
        domain = item.url.lower().split("://")[-1].split("/")[0]
        if domain.startswith("www."):
            domain = domain[4:]
        return any(_domain_matches(domain, d) for d in self.OFFICIAL_DOMAINS)

    def _cross_reference(self, item: NewsItem, max_results: int = 8) -> list[str]:
        """多源交叉验证 — 搜索引擎搜索同一事件关键词.

        提取核心实体词，搜索后统计独立报道源数量。
        同时使用 DuckDuckGo 和 Bing 搜索，合并去重。
        """
        query = self._extract_query(item)
        if not query:
            return []

        all_sources: list[str] = []

        # DuckDuckGo
        try:
            ddg_sources = self._search_duckduckgo(query, max_results)
            all_sources.extend(ddg_sources)
        except Exception as e:
            logger.debug(f"交叉验证 DDG 搜索失败: {e}")

        # Bing
        try:
            bing_sources = self._search_bing(query, max_results)
            all_sources.extend(bing_sources)
        except Exception as e:
            logger.debug(f"交叉验证 Bing 搜索失败: {e}")

        # 去重: 同域名的只算一个源
        unique_domains: set[str] = set()
        unique_sources: list[str] = []
        for src in all_sources:
            domain = self._extract_domain(src)
            if domain and domain not in unique_domains:
                unique_domains.add(domain)
                unique_sources.append(src)

        # 排除原新闻来源
        original_domain = self._extract_domain(item.url)
        filtered = [s for s in unique_sources
                    if self._extract_domain(s) != original_domain]

        return filtered[:max_results]

    def _detect_conflict(self, item: NewsItem, cross_sources_texts: list[str]) -> bool:
        """检测交叉源中是否存在与新闻核心主张明显矛盾的内容.

        通过关键词对检测矛盾表述（如"加息"vs"降息"）。
        """
        item_text = f"{item.title} {item.summary}".lower()
        for pair_a, pair_b in self.CONFLICT_PAIRS:
            # 如果原新闻包含 pair_a，而交叉源包含 pair_b
            if pair_a in item_text:
                for src_text in cross_sources_texts:
                    if pair_b in src_text.lower():
                        logger.debug(f"冲突检测: '{pair_a}' vs '{pair_b}' in {item.title[:40]}")
                        return True
            # 反之亦然
            if pair_b in item_text:
                for src_text in cross_sources_texts:
                    if pair_a in src_text.lower():
                        logger.debug(f"冲突检测: '{pair_b}' vs '{pair_a}' in {item.title[:40]}")
                        return True
        return False

    def _verify_fomc_meeting(self, item: NewsItem) -> FactCheckResult | None:
        """验证 FOMC 会议日期是否匹配官方日历.

        从联邦储备官网获取 FOMC 日历，解析出所有会议日期，
        检查新闻声称的日期是否落在某个官方会议窗口内（±1 天容差）。
        """
        try:
            with get_proxied_client(timeout=20, follow_redirects=True) as client:
                resp = client.get(
                    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                        ),
                    },
                )
                resp.raise_for_status()
                html = resp.text

            import re as _re
            from datetime import datetime as _dt

            # 从标题中提取声称的日期：如 "June 16-17, 2026" 或 "June 17, 2026"
            claimed_dates: list[_dt] = []
            month_map = {
                "january": 1, "february": 2, "march": 3, "april": 4,
                "may": 5, "june": 6, "july": 7, "august": 8,
                "september": 9, "october": 10, "november": 11, "december": 12,
            }
            for match in _re.finditer(
                r'(\w+)\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?,?\s+(\d{4})',
                item.title,
                _re.I,
            ):
                month = month_map.get(match.group(1).lower())
                start_day = int(match.group(2))
                year = int(match.group(4))
                if month and year:
                    claimed_dates.append(_dt(year, month, start_day))

            if not claimed_dates:
                return None

            # 解析官方日历中所有会议日期
            official_dates: list[_dt] = []
            for match in _re.finditer(
                r'(\w+)\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?,?\s+(\d{4})',
                html,
                _re.I,
            ):
                month = month_map.get(match.group(1).lower())
                start_day = int(match.group(2))
                year = int(match.group(4))
                if month and year and year >= _dt.now().year:
                    official_dates.append(_dt(year, month, start_day))

            # 检查是否有声称日期落在某个官方日期 ±1 天内
            for claimed in claimed_dates:
                for official in official_dates:
                    if abs((claimed - official).days) <= 1:
                        return FactCheckResult(
                            news_item=item,
                            status=VerificationStatus.CONFIRMED,
                            confidence=0.85,
                            check_method="official_fomc_calendar",
                        )

            # 月份出现但日期不匹配：仍算部分验证
            html_lower = html.lower()
            for claimed in claimed_dates:
                month_year = f"{claimed.strftime('%B').lower()} {claimed.year}"
                if month_year in html_lower:
                    return FactCheckResult(
                        news_item=item,
                        status=VerificationStatus.UNVERIFIED,
                        confidence=0.5,
                        check_method="fomc_month_matched_date_mismatch",
                    )
        except Exception as e:
            logger.debug(f"FOMC 日历验证失败: {e}")

        return None

    def _verify_sge_gold_price(self, item: NewsItem) -> FactCheckResult | None:
        """验证上金所 Au99.99 价格数据.

        目前为 stub：如果 URL 包含 sge.com.cn 则视为可信，否则返回 None。
        未来可扩展为抓取 SGE 实时行情页面。
        """
        if "sge.com.cn" in item.url.lower():
            return FactCheckResult(
                news_item=item,
                status=VerificationStatus.CONFIRMED,
                confidence=0.85,
                check_method="sge_official_url",
            )
        return None

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
            links = re.findall(r'href="(https?://[^"]+)"', html)
            for link in links[:max_results * 2]:
                if any(domain in link for domain in [
                    "duckduckgo.com", "duck.co", "spreadprivacy.com"
                ]):
                    continue
                sources.append(link)
                if len(sources) >= max_results:
                    break
        except Exception as e:
            logger.debug(f"交叉验证搜索失败: {e}")

        return sources

    def _search_bing(self, query: str, max_results: int) -> list[str]:
        """通过 Bing 搜索获取结果源列表."""
        url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
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

            links = re.findall(r'href="(https?://[^"]+)"', html)
            for link in links[:max_results * 2]:
                if "bing.com" in link or "microsoft.com" in link:
                    continue
                sources.append(link)
                if len(sources) >= max_results:
                    break
        except Exception as e:
            logger.debug(f"交叉验证搜索失败: {e}")

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

        return age_days >= 0 and not (item.is_breaking and age_days > 7)

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

    修改 items 的 metadata 字段，增加 verification_status、confidence、source_tier。
    通过 items 与 results 的顺序一一对应，避免标题重复导致覆盖。
    """
    for idx, item in enumerate(items):
        if idx >= len(results):
            break
        result = results[idx]
        item.metadata["verification_status"] = result.status.value
        item.metadata["verification_confidence"] = result.confidence
        item.metadata["verification_method"] = result.check_method
        item.metadata["cross_sources"] = result.cross_sources
        # 补充 source_tier（如果 news.py 没设置）
        if not item.metadata.get("source_tier"):
            item.metadata["source_tier"] = get_source_tier(item.source, item.url)

    return items


def filter_unverified_news(
    items: list[NewsItem],
    min_confidence: float = 0.2,
) -> list[NewsItem]:
    """过滤掉可信度过低的新闻.

    丢弃: false 或 disputed 且 confidence < min_confidence
    保留: 其余（confidence 由 NewsSignalGenerator 在打分阶段使用）
    """
    filtered: list[NewsItem] = []
    for item in items:
        status = item.metadata.get("verification_status", "unverified")
        confidence = item.metadata.get("verification_confidence", 0.0)

        if status == VerificationStatus.FALSE.value:
            continue
        if status == VerificationStatus.DISPUTED.value and confidence < 0.3:
            continue

        filtered.append(item)

    return filtered


def format_verification_tag(item: NewsItem) -> str:
    """根据新闻元数据返回格式化验证标签.

    Returns:
        例如: [verified: T0], [verified: T2], [unverified], [disputed]
    """
    v_status = item.metadata.get("verification_status", "unverified")
    tier = item.metadata.get("source_tier", "unknown")

    if v_status == "disputed":
        return "[disputed]"
    if v_status == "confirmed":
        return f"[verified: {tier}]"
    return "[unverified]"
