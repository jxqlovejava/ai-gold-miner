"""新闻数据抓取 — 多源聚合: NewsAPI / anysearch / 搜索引擎."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.source_tiers import get_source_tier
from gold_miner.utils.http_fallback import fallback_get, fallback_post


def _is_retryable_error(e: Exception) -> bool:
    """判断异常是否值得重试."""
    return isinstance(e, (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.NetworkError,
        httpx.RemoteProtocolError,  # SSL EOF / 连接被重置等传输层问题
    ))


def _should_retry_status(status: int) -> bool:
    """判断 HTTP 状态码是否值得重试."""
    return status in (429, 500, 502, 503, 504)


def _sleep_backoff(attempt: int, base: float = 1.0) -> None:
    """指数退避休眠."""
    time.sleep(base * (2 ** attempt))


# 贸易/地缘大事件窗口 (2026-08-25): 美加谈判破裂(8/21-22 50%关税)等事件跨周末,
# 24h 窗口必漏 — 重大事件主题放宽到 72h
_BIG_EVENT_WINDOW = 72

# 宽松过滤词: 保留可能影响金价的新闻 (2026-08-25 补加拿大/贸易词条)
_IMPACT_WORDS = (
    "gold", "xau", "bullion", "precious metal",
    "fed", "rate", "inflation", "cpi", "ppi",
    "payroll", "nfp", "nonfarm", "unemployment", "job",
    "iran", "middle east", "war", "conflict", "geopolitical",
    "central bank", "stimulus", "recession", "dollar", "treasury",
    "tariff", "sanction", "crisis", "safe haven", "避险",
    "silver", "metal", "commodity", "precious",
    "canada", "trade deal", "trade talks", "trade war", "usmca",
)


def _merge_news_items(*pools: list[NewsItem]) -> list[NewsItem]:
    """多源新闻合并去重 (URL 优先, 无 URL 用标题)."""
    merged: list[NewsItem] = []
    seen: set[str] = set()
    for pool in pools:
        for it in pool:
            key = it.url or (it.title or "").strip().lower()[:80]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(it)
    return merged


# 前瞻/预期类事件词 — 标题含这些词说明事件「尚未落地」, 需标注避免误导
_FORWARD_LOOKING_WORDS = (
    "expected", "ahead of", "plans to", "set to", "poised",
    "to impose", "to unveil", "anticipated", "reportedly",
    "considering", "mulling", "weighing", "will announce",
    "is expected", "set for", "in talks to",
)


def _mark_forward_looking(items: list[NewsItem]) -> None:
    """给前瞻/预期类新闻打 forward_looking 标记."""
    for it in items:
        text = f"{it.title} {it.summary}".lower()
        it.metadata["forward_looking"] = any(
            w in text for w in _FORWARD_LOOKING_WORDS
        )


@dataclass
class NewsItem:
    """单条新闻."""

    title: str
    source: str
    published_at: datetime
    url: str = ""
    summary: str = ""
    sentiment: float = 0.0  # -1 ~ +1
    keywords: list[str] = field(default_factory=list)
    is_breaking: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class AnySearchFetcher:
    """使用 anysearch API 获取新闻.

    API: https://api.anysearch.com/mcp (JSON-RPC 2.0)
    支持匿名访问(限流较低)或配置 API key.
    """

    ENDPOINT = "https://api.anysearch.com/mcp"

    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or getattr(settings, "anysearch_api_key", "")

    def search(
        self,
        query: str,
        max_results: int = 10,
        freshness: str = "day",
        content_types: list[str] | None = None,
        zone: str = "intl",
    ) -> list[NewsItem]:
        """调用 anysearch API 搜索新闻."""
        content_types = content_types or ["news"]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {
                    "query": query,
                    "content_types": content_types,
                    "freshness": freshness,
                    "max_results": max_results,
                    "zone": zone,
                },
            },
        }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = fallback_post(self.ENDPOINT, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                if "error" in data:
                    logger.warning(f"anysearch API错误: {data['error']}")
                    return []

                result = data.get("result", {})
                content = result.get("content", [])
                text = ""
                for item in content:
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        break

                parsed = self._parse_anysearch_results(text)
                # 配额耗尽或限流时直接返回，不再重试
                if not parsed and text and any(
                    kw in text.lower()
                    for kw in [
                        "quota exhausted", "quota exceeded", "limit reached",
                        "too many requests", "rate limit", "payment required", "402", "429",
                    ]
                ):
                    return []
                return parsed
            except Exception as e:
                last_error = e
                if not _is_retryable_error(e):
                    break
                if attempt < 2:
                    logger.warning(f"anysearch请求失败 (尝试 {attempt + 1}/3): {e}, 即将重试")
                    _sleep_backoff(attempt)
                else:
                    logger.warning(f"anysearch请求失败 (尝试 {attempt + 1}/3): {e}")

        logger.warning(f"anysearch请求最终失败: {last_error}")
        return []

    def _parse_anysearch_results(self, text: str) -> list[NewsItem]:
        """解析 anysearch 返回的文本结果."""
        items: list[NewsItem] = []
        if not text:
            return items

        # 检测配额耗尽或 API 错误
        quota_exhausted_keywords = [
            "daily_free_quota_exhausted",
            "free quota is exhausted",
            "quota exhausted",
            "quota exceeded",
            "limit reached",
            "too many requests",
            "rate limit",
            "api error",
            "service unavailable",
            "payment required",
            "402",
            "429",
        ]
        lower_text = text.lower()
        if any(kw in lower_text for kw in quota_exhausted_keywords):
            logger.warning(f"anysearch 配额耗尽或 API 错误: {text[:200]}")
            return items

        # anysearch 返回 Markdown 格式或 JSON 格式
        # 尝试解析 JSON
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for entry in data:
                    items.append(self._entry_to_item(entry))
            elif isinstance(data, dict) and "results" in data:
                for entry in data["results"]:
                    items.append(self._entry_to_item(entry))
        except json.JSONDecodeError:
            # 非 JSON -> 按 Markdown 块解析 (2026-08-25 重写):
            # 旧逐行解析把每行 >20 字符都当独立条目, "- **URL**: ..." 行变垃圾条目,
            # 且 URL 从未被提取 -> FactChecker 交叉验证(按域名多源确认)系统性全挂.
            # 新解析: "### N. 标题" 开新条目, URL 行回填当前条目, 其余 "- " 行作摘要.
            items = self._parse_anysearch_markdown(text)

        return items

    def _parse_anysearch_markdown(self, text: str) -> list[NewsItem]:
        """按块解析 anysearch Markdown 搜索结果.

        格式:
            ## Search Results (N results, XXXms)
            ### 1. Title ...
            - **URL**: https://...
            - summary line ...
        """
        items: list[NewsItem] = []
        current: NewsItem | None = None

        def _flush() -> None:
            nonlocal current
            if current is not None and current.title.strip():
                items.append(current)
            current = None

        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            # 结构行: 结果统计头 / 分隔线
            if line.startswith("## ") or line.startswith("---"):
                continue
            # 结果条目标题行: "### 1. Title"
            m = re.match(r"^#{3,6}\s*\d*\.?\s*(.+)$", line)
            if m:
                _flush()
                current = NewsItem(
                    title=m.group(1).strip(),
                    source="anysearch",
                    published_at=datetime.now(),
                )
                continue
            # URL 行: "- **URL**: https://..." -> 回填当前条目 (来源=域名, 定层级)
            m = re.match(r"^-?\s*\*?\*?URL\*?\*?:?\s*(https?://\S+)$", line)
            if m and current is not None:
                current.url = m.group(1)
                try:
                    domain = urlparse(m.group(1)).netloc.replace("www.", "")
                    if domain:
                        current.source = domain
                        current.metadata["source_tier"] = get_source_tier(domain, m.group(1))
                except Exception:
                    pass
                continue
            # 摘要行: "- text ..." -> 追加到当前条目摘要
            m = re.match(r"^[-*]\s+(.+)$", line)
            if m and current is not None:
                snippet = m.group(1).strip()
                if not current.summary:
                    current.summary = snippet
                elif len(current.summary) < 400:
                    current.summary += " " + snippet
                continue
            # 纯 URL 行 (旧格式兼容)
            if line.startswith(("http://", "https://")):
                _flush()
                current = NewsItem(
                    title=line, source="anysearch", published_at=datetime.now(), url=line
                )
                continue
            # 裸文本行: 有当前条目作摘要; 无当前条目且足够长时按独立条目兜底
            # (兼容非 Markdown 的纯文本响应, 如单行标题)
            if current is not None:
                if not current.summary:
                    current.summary = line
                elif len(current.summary) < 400:
                    current.summary += " " + line
            elif len(line) > 20:
                current = NewsItem(
                    title=line, source="anysearch", published_at=datetime.now()
                )
                _flush()

        _flush()
        return items

    def _entry_to_item(self, entry: dict[str, Any]) -> NewsItem:
        """将 anysearch 条目转为 NewsItem."""
        published = entry.get("published", entry.get("date", ""))
        try:
            if isinstance(published, str):
                published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
            else:
                published_at = datetime.now()
        except ValueError:
            published_at = datetime.now()

        source = entry.get("source", entry.get("domain", "anysearch"))
        url = entry.get("url", entry.get("link", ""))
        item = NewsItem(
            title=entry.get("title", entry.get("name", "")),
            source=source,
            published_at=published_at,
            url=url,
            summary=entry.get("summary", entry.get("snippet", entry.get("description", ""))),
        )
        item.metadata["source_tier"] = get_source_tier(source, url)
        return item


class SearchEngineFetcher:
    """使用搜索引擎直接抓取新闻 — 无需 API key."""

    # ponytail: 进程级熔断——单主题 2 次超时=网络不可达，后续主题直接跳过
    # (2026-08-23 事故: 8 主题串行各重试2次×12s 白耗 ~186s 零产出)
    _ddg_circuit_open = False

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def fetch_from_duckduckgo(self, query: str, max_results: int = 10) -> list[NewsItem]:
        """从 DuckDuckGo 抓取搜索结果（带重试+进程级熔断）."""
        if SearchEngineFetcher._ddg_circuit_open:
            return []
        url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
        # DuckDuckGo 在本环境频繁超时，使用较短超时和较少重试
        saw_network_error = False
        for attempt in range(2):
            try:
                resp = fallback_get(url, headers=self._HEADERS, timeout=5)
                if _should_retry_status(resp.status_code):
                    if attempt < 1:
                        logger.warning(f"DuckDuckGo 返回 {resp.status_code}，即将重试")
                        _sleep_backoff(attempt)
                    continue
                return self._parse_duckduckgo_html(resp.text, max_results)
            except Exception as e:
                if not _is_retryable_error(e):
                    break
                saw_network_error = True
                if attempt < 1:
                    logger.warning(f"DuckDuckGo抓取失败 (尝试 {attempt + 1}/2): {e}, 即将重试")
                    _sleep_backoff(attempt)
                else:
                    logger.warning(f"DuckDuckGo抓取失败 (尝试 {attempt + 1}/2): {e}")
        if saw_network_error:
            SearchEngineFetcher._ddg_circuit_open = True
            logger.warning("DuckDuckGo 连续网络失败，本轮后续查询熔断跳过")
        return []

    def fetch_from_bing(self, query: str, max_results: int = 10) -> list[NewsItem]:
        """从 Bing 抓取搜索结果（带重试）."""
        url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
        for attempt in range(3):
            try:
                resp = fallback_get(url, headers=self._HEADERS, timeout=30)
                if _should_retry_status(resp.status_code):
                    if attempt < 2:
                        logger.warning(f"Bing 返回 {resp.status_code}，即将重试")
                        _sleep_backoff(attempt)
                    continue
                return self._parse_bing_html(resp.text, max_results)
            except Exception as e:
                if not _is_retryable_error(e):
                    break
                if attempt < 2:
                    logger.warning(f"Bing抓取失败 (尝试 {attempt + 1}/3): {e}, 即将重试")
                    _sleep_backoff(attempt)
                else:
                    logger.warning(f"Bing抓取失败 (尝试 {attempt + 1}/3): {e}")
        return []

    def _parse_bing_html(self, html: str, max_results: int) -> list[NewsItem]:
        """解析 Bing HTML 结果."""
        soup = BeautifulSoup(html, "html.parser")
        items: list[NewsItem] = []

        # Bing 结果在 .b_algo 类中
        for result in soup.find_all("li", class_="b_algo")[:max_results]:
            title_tag = result.find("h2")
            if not title_tag:
                continue

            a_tag = title_tag.find("a")
            title = a_tag.get_text(strip=True) if a_tag else title_tag.get_text(strip=True)
            url = a_tag.get("href", "") if a_tag else ""

            summary_tag = result.find("p")
            summary = summary_tag.get_text(strip=True) if summary_tag else ""

            item = NewsItem(
                title=title,
                source="Bing",
                published_at=datetime.now(),
                url=url,
                summary=summary,
            )
            item.metadata["source_tier"] = get_source_tier("Bing", url)
            items.append(item)

        return items

    def _parse_duckduckgo_html(self, html: str, max_results: int) -> list[NewsItem]:
        """解析 DuckDuckGo HTML 结果."""
        soup = BeautifulSoup(html, "html.parser")
        items: list[NewsItem] = []

        # DuckDuckGo HTML 版结果在 .result 类中
        for result in soup.find_all("div", class_="result")[:max_results]:
            title_tag = result.find("a", class_="result__a")
            snippet_tag = result.find("a", class_="result__snippet")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            url = title_tag.get("href", "")
            summary = snippet_tag.get_text(strip=True) if snippet_tag else ""

            item = NewsItem(
                title=title,
                source="DuckDuckGo",
                published_at=datetime.now(),
                url=url,
                summary=summary,
            )
            item.metadata["source_tier"] = get_source_tier("DuckDuckGo", url)
            items.append(item)

        return items

    def fetch_from_bing_news(self, query: str, max_results: int = 10) -> list[NewsItem]:
        """从 Bing News 抓取新闻结果（区别于通用 Bing 搜索，带重试）."""
        url = f"https://www.bing.com/news/search?q={query.replace(' ', '+')}"
        for attempt in range(3):
            try:
                resp = fallback_get(url, headers=self._HEADERS, timeout=30)
                if _should_retry_status(resp.status_code):
                    if attempt < 2:
                        logger.warning(f"Bing News 返回 {resp.status_code}，即将重试")
                        _sleep_backoff(attempt)
                    continue
                return self._parse_bing_news_html(resp.text, max_results)
            except Exception as e:
                if not _is_retryable_error(e):
                    break
                if attempt < 2:
                    logger.warning(f"Bing News抓取失败 (尝试 {attempt + 1}/3): {e}, 即将重试")
                    _sleep_backoff(attempt)
                else:
                    logger.warning(f"Bing News抓取失败 (尝试 {attempt + 1}/3): {e}")
        return []

    def _parse_bing_news_html(self, html: str, max_results: int) -> list[NewsItem]:
        """解析 Bing News HTML 结果."""
        soup = BeautifulSoup(html, "html.parser")
        items: list[NewsItem] = []

        # Bing News 结果卡片结构
        for card in soup.find_all("div", class_="news-card")[:max_results]:
            a_tag = card.find("a", class_="title")
            if not a_tag:
                a_tag = card.find("a")
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            url = a_tag.get("href", "")

            # 来源
            source_tag = card.find("div", class_="source")
            source = source_tag.get_text(strip=True) if source_tag else "Bing News"

            # 摘要
            snippet_tag = card.find("div", class_="snippet")
            summary = snippet_tag.get_text(strip=True) if snippet_tag else ""

            item = NewsItem(
                title=title,
                source=source,
                published_at=datetime.now(),
                url=url,
                summary=summary,
            )
            item.metadata["source_tier"] = get_source_tier(source, url)
            items.append(item)

        # fallback: 通用新闻链接解析
        if not items:
            for a in soup.find_all("a", href=True)[:max_results * 2]:
                href = a.get("href", "")
                if not href.startswith("http"):
                    continue
                title = a.get_text(strip=True)
                if not title or len(title) < 10:
                    continue
                item = NewsItem(
                    title=title,
                    source="Bing News",
                    published_at=datetime.now(),
                    url=href,
                    summary="",
                )
                item.metadata["source_tier"] = get_source_tier("Bing News", href)
                items.append(item)
                if len(items) >= max_results:
                    break

        return items

    def fetch_multi(self, queries: list[str], max_results: int = 10) -> list[NewsItem]:
        """对多个查询同时跑 DuckDuckGo 和 Bing，去重后合并."""
        all_items: list[NewsItem] = []
        seen_urls: set[str] = set()

        for idx, q in enumerate(queries):
            # 查询间短暂间隔，降低被限流风险
            if idx > 0:
                time.sleep(0.5)

            # DuckDuckGo
            try:
                ddg_items = self.fetch_from_duckduckgo(q, max_results=max_results)
                for item in ddg_items:
                    url_key = item.url or f"{item.title}|{item.source}"
                    if url_key not in seen_urls:
                        seen_urls.add(url_key)
                        all_items.append(item)
            except Exception as e:
                logger.debug(f"fetch_multi DDG 失败 [{q}]: {e}")

            # Bing News
            bing_news_ok = False
            try:
                bing_items = self.fetch_from_bing_news(q, max_results=max_results)
                if bing_items:
                    bing_news_ok = True
                for item in bing_items:
                    url_key = item.url or f"{item.title}|{item.source}"
                    if url_key not in seen_urls:
                        seen_urls.add(url_key)
                        all_items.append(item)
            except Exception as e:
                logger.debug(f"fetch_multi Bing News 失败 [{q}]: {e}")

            # Bing News 失败时，用通用 Bing 搜索兜底
            if not bing_news_ok:
                try:
                    bing_items = self.fetch_from_bing(q, max_results=max_results)
                    for item in bing_items:
                        url_key = item.url or f"{item.title}|{item.source}"
                        if url_key not in seen_urls:
                            seen_urls.add(url_key)
                            all_items.append(item)
                except Exception as e:
                    logger.debug(f"fetch_multi Bing 兜底失败 [{q}]: {e}")

        return all_items[:max_results]


class JdjrNewsFetcher:
    """京东金融黄金资讯 (免登录, jdgold 数据层封装).

    数据源: gold_miner.data.jdgold_client.fetch_news (jdjr_query_news --no-flash, 合并去重)。
    集成背景: docs/analysis/jdgold-integration-analysis-2026-08-12.md E3 (news_monitor 加源)。
    """

    SOURCE = "jdjr"
    SOURCE_TIER = "T1"  # 京东金融官方公开数据

    def fetch_latest(self, query: str = "黄金", max_results: int = 5) -> list[NewsItem]:
        """抓取京东金融黄金资讯 (快讯+资讯合并去重)."""
        try:
            from gold_miner.data.jdgold_client import fetch_news

            raw = fetch_news(keyword=query, size=max_results)
            if not raw:
                return []
            items: list[NewsItem] = []
            for n in raw:
                title = str(n.get("title") or "").strip()
                if not title or len(title) < 5:
                    continue
                url = str(n.get("url") or "")
                published_at = datetime.now()
                ts = n.get("time")
                if ts:
                    try:
                        published_at = datetime.fromisoformat(str(ts))
                    except ValueError:
                        pass
                item = NewsItem(
                    title=title,
                    source=self.SOURCE,
                    published_at=published_at,
                    url=url,
                    summary=str(n.get("content") or "").strip(),
                )
                item.metadata["source_tier"] = self.SOURCE_TIER
                items.append(item)
            return items
        except Exception:
            return []


class NewsFetcher:
    """新闻数据获取器 — 多源聚合.

    数据源优先级:
    1. NewsAPI (需 API key, 质量最高)
    2. anysearch (无需 key, 匿名限流)
    3. 搜索引擎 (DuckDuckGo/Bing, 无需 key)
    """

    BULLISH_KEYWORDS = [
        "上涨", "rise", "rally", "surge", "gain", "boost", "bullish", "breakout",
        "突破", "利好", "support", "支撑", "buy", "买入", "accumulate", "囤积",
        "safe haven", "避险", "hedge", "inflation hedge", "央行购金", "demand",
    ]
    BEARISH_KEYWORDS = [
        "下跌", "fall", "drop", "decline", "plunge", "crash", "bearish", "dump",
        "跌破", "利空", "resistance", "阻力", "sell", "卖出", "profit taking",
        "获利了结", "overbought", "超买", "correction", "回调", "recession fear",
    ]
    BREAKING_KEYWORDS = [
        "breaking", "突发", "紧急", "urgent", "FOMC", "Fed", "CPI", "NFP",
        "payroll", "nonfarm", "rate cut", "rate hike", "降息", "加息",
        "war", "conflict", "战争", "Iran", "Middle East", "sanction", "制裁",
        "crisis", "危机", "geopolitical", "unemployment", "strike",
        # 2026-08-25 补: 贸易战/关税/谈判类 (美加谈判破裂50%关税此前 break_count 仅命中
        # "war"=1, 不达 >=2 阈值, 进不了「重大事件」信号, 只在聚合里被淹没)
        "trade war", "tariff", "tariffs", "trade talks", "trade deal",
        "canada", "谈判破裂", "贸易战", "关税",
    ]

    def __init__(self) -> None:
        self.newsapi_key = settings.news_api_key
        self.anysearch = AnySearchFetcher()
        self.search_engine = SearchEngineFetcher()
        self.jdjr = JdjrNewsFetcher()

    # 进程内 TTL 缓存: news 信号生成与 news_raw 并行拉取共用, 避免重复调 NewsAPI/anysearch
    # (key=(query,hours) → (ts, items)); 同一 pipeline 进程内有效
    _fetch_cache: dict[tuple[str, int], tuple[float, list[NewsItem]]] = {}
    _fetch_cache_lock = threading.Lock()
    _FETCH_CACHE_TTL_SECONDS = 300.0
    # 磁盘缓存: 跨进程/跨 scan 复用, 同一天多次分析直接读缓存不再碰 NewsAPI
    # (fast-analysis: 完整分析 ≤1min, NewsAPI 网络抖动不阻塞)
    _DISK_CACHE_TTL_SECONDS = 1800.0  # 30 分钟

    @staticmethod
    def _newsitem_to_dict(item: NewsItem) -> dict[str, Any]:
        """NewsItem → dict (磁盘缓存持久化)."""
        return {
            "title": item.title,
            "source": item.source,
            "published_at": item.published_at.isoformat(),
            "url": item.url,
            "summary": item.summary,
            "sentiment": item.sentiment,
            "keywords": item.keywords,
            "is_breaking": item.is_breaking,
            "metadata": item.metadata,
        }

    @staticmethod
    def _dict_to_newsitem(d: dict[str, Any]) -> NewsItem:
        """dict → NewsItem (磁盘缓存反序列化)."""
        try:
            published_at = datetime.fromisoformat(d["published_at"])
        except (KeyError, ValueError, TypeError):
            published_at = datetime.now()
        return NewsItem(
            title=d.get("title", ""),
            source=d.get("source", "Unknown"),
            published_at=published_at,
            url=d.get("url", ""),
            summary=d.get("summary", ""),
            sentiment=float(d.get("sentiment", 0.0)),
            keywords=list(d.get("keywords", [])),
            is_breaking=bool(d.get("is_breaking", False)),
            metadata=dict(d.get("metadata", {})),
        )

    @property
    def _disk_cache_path(self) -> Path:
        return settings.private_data_path / "cache" / "news_fetch.json"

    def _load_disk_cache(self) -> dict[str, dict[str, Any]]:
        """读取磁盘新闻缓存 (跨进程复用)."""
        try:
            p = self._disk_cache_path
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"news 磁盘缓存读取失败: {e}")
        return {}

    def _save_disk_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        """写磁盘新闻缓存, 并清理过期条目."""
        try:
            now = time.time()
            cache = {k: v for k, v in cache.items()
                     if now - v.get("ts", 0) <= NewsFetcher._DISK_CACHE_TTL_SECONDS}
            p = self._disk_cache_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug(f"news 磁盘缓存写入失败: {e}")

    def fetch_latest(
        self,
        query: str = "gold OR XAU OR FED OR 美联储 OR 黄金",
        hours: int = 24,
        max_results: int = 10,
    ) -> list[NewsItem]:
        """抓取最新新闻 — 多源回退 (进程内 TTL 缓存去重).

        同一进程内 news 信号与 news_raw 并发调用时只拉取一次 (double-checked locking);
        慢源/超时重试不会被重复执行。
        """
        cache_key = (query, hours)
        now = time.time()

        # 快路径 1: 进程内缓存 (dict 读是原子的, 命中即返回)
        hit = NewsFetcher._fetch_cache.get(cache_key)
        if hit and now - hit[0] <= NewsFetcher._FETCH_CACHE_TTL_SECONDS:
            return hit[1]

        # 快路径 2: 磁盘缓存 (跨进程复用, 30min TTL) — 同一天多次分析不碰 NewsAPI
        disk_key = f"{query}||{hours}"
        disk_hit = self._load_disk_cache().get(disk_key)
        if disk_hit and now - disk_hit.get("ts", 0) <= NewsFetcher._DISK_CACHE_TTL_SECONDS:
            items = [NewsFetcher._dict_to_newsitem(i) for i in disk_hit.get("items", [])]
            _mark_forward_looking(items)  # 缓存路径同样打前瞻标注 (2026-08-25)
            NewsFetcher._fetch_cache[cache_key] = (now, items)
            return items

        with NewsFetcher._fetch_cache_lock:
            # 已持锁, 直接读内部状态 (threading.Lock 非重入, 不能在此调 _from_cache)
            hit = NewsFetcher._fetch_cache.get(cache_key)
            if hit and time.time() - hit[0] <= NewsFetcher._FETCH_CACHE_TTL_SECONDS:
                return hit[1]
            # 持锁拉取: 并发调用方等待本次完成后命中缓存
            items = self._fetch_latest_uncached(query, hours, max_results)
            NewsFetcher._fetch_cache[cache_key] = (time.time(), items)
            # 非空结果写磁盘缓存 (避免缓存失败态)
            if items:
                disk = self._load_disk_cache()
                disk[disk_key] = {"ts": time.time(), "items": [NewsFetcher._newsitem_to_dict(i) for i in items]}
                self._save_disk_cache(disk)
            return items

    def _fetch_latest_uncached(
        self,
        query: str,
        hours: int,
        max_results: int,
    ) -> list[NewsItem]:
        """无缓存的多源抓取 (news 信号/raw 首个调用真正执行).

        2026-08-25 重构: NewsAPI 与 anysearch 并行合并去重, 不再 NewsAPI 非空即短路 —
        旧逻辑使 f1de712 加的 anysearch 贸易主题(美加谈判/关税)永远不执行,
        8/25「美加谈判破裂50%关税 / 美对伊制裁」重要新闻 0 覆盖.
        """
        # 1. NewsAPI (国内直连可用, 质量最高) + anysearch 并行, 合并去重
        newsapi_items: list[NewsItem] = []
        if self.newsapi_key:
            newsapi_items = self._fetch_newsapi_multi(hours)
            if newsapi_items:
                logger.info(f"NewsAPI 返回 {len(newsapi_items)} 条新闻")
            else:
                logger.warning("NewsAPI 多主题查询过滤后 0 条 -> 依赖 anysearch/降级")

        anysearch_items = self._fetch_anysearch_multi(hours, max_results)
        if anysearch_items:
            logger.info(f"anysearch 返回 {len(anysearch_items)} 条新闻")

        merged = _merge_news_items(newsapi_items, anysearch_items)
        # 统一宽松过滤 (anysearch 主题查询会带入娱乐/无关噪音, 如 Vulture/HuffPost)
        merged = [
            i for i in merged
            if any(w in (i.title + " " + i.summary).lower() for w in _IMPACT_WORDS)
        ]
        if merged:
            _mark_forward_looking(merged)
            return merged

        # 2. 并行尝试 fallback 源 (jdjr / 搜索引擎多查询), 保留优先级取首个成功.
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _try_jdjr() -> list[NewsItem]:
            # jdgold 官方黄金资讯兜底 (免登录, 中文快讯, E3 2026-08-13)
            return self.jdjr.fetch_latest(query="黄金", max_results=max_results) or []

        # 3. 搜索引擎回退 — 多查询并行（不硬编码具体月份/年份）
        target_queries = [
            "gold price XAU USD today",
            "FOMC Fed gold",
            "Iran US nuclear deal gold",
            "central bank gold demand",
            "黄金价格 今日 走势",
            "美联储 利率决议 黄金",
            "伊朗 和谈 黄金",
            "央行 购金 黄金",
        ]

        def _try_search_engine() -> list[NewsItem]:
            return self.search_engine.fetch_multi(target_queries, max_results=max_results) or []

        def _try_ddg() -> list[NewsItem]:
            return self.search_engine.fetch_from_duckduckgo("gold price news", max_results) or []

        def _try_bing() -> list[NewsItem]:
            return self.search_engine.fetch_from_bing("gold price news", max_results) or []

        # fallback 源并行; 拿首个成功即返回, 慢源不阻塞 (shutdown(wait=False) 不等待后台慢任务)
        executor = _TPE(max_workers=3, thread_name_prefix="news-fallback")
        try:
            f_jdjr = executor.submit(_try_jdjr)
            f_se = executor.submit(_try_search_engine)
            for fut, label in [
                (f_jdjr, "jdgold 资讯"),
                (f_se, "搜索引擎多查询"),
            ]:
                items = fut.result()
                if items:
                    logger.info(f"{label} 返回 {len(items)} 条新闻")
                    executor.shutdown(wait=False, cancel_futures=True)
                    return items

            # 4/5. 单查询兜底并行 (DuckDuckGo / Bing)
            f_ddg = executor.submit(_try_ddg)
            f_bing = executor.submit(_try_bing)
            for fut, label in [(f_ddg, "DuckDuckGo"), (f_bing, "Bing")]:
                items = fut.result()
                if items:
                    logger.info(f"{label} 返回 {len(items)} 条新闻")
                    executor.shutdown(wait=False, cancel_futures=True)
                    return items
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        logger.warning("所有新闻源均无法获取数据")
        return []

    def _fetch_newsapi_multi(self, hours: int) -> list[NewsItem]:
        """NewsAPI 多主题查询 → 合并去重 → 宽松过滤.

        2026-08-25 修复:
        - 贸易查询词从 'trade tariff OR trade war OR sanctions' 收紧为精确短语,
          避免命中 NBA trade/股市噪音 (美加谈判破裂 0 覆盖根因之一);
        - 贸易/地缘主题放宽到 max(hours, 72h) 窗口 — 美加谈判破裂(8/21-22)跨周末超 24h 必漏.
        """
        today = datetime.now()
        is_nfp_day = today.weekday() == 4 and 1 <= today.day <= 7
        nfp_query = (
            f"nonfarm payrolls {today.strftime('%B %Y')} results"
            if is_nfp_day else "nonfarm payrolls"
        )
        # (标签, 查询词, 窗口)
        queries: list[tuple[str, str, int]] = [
            ("gold", "gold price OR gold market OR gold forecast", hours),
            ("宏观", f"{nfp_query} OR Fed rate decision OR CPI inflation OR unemployment", hours),
            ("地缘", "Iran conflict OR Middle East war OR geopolitical crisis", max(hours, _BIG_EVENT_WINDOW)),
            ("贸易", '"trade war" OR "trade talks" OR "trade deal" OR tariff', max(hours, _BIG_EVENT_WINDOW)),
        ]
        all_items: list[NewsItem] = []
        seen_urls: set[str] = set()
        # 并行拉取 4 条 query: NewsAPI 挂时最坏耗时收敛到单次超时×重试,
        # 而非 4 倍串行 (fast-analysis: 完整分析 ≤1min)
        with ThreadPoolExecutor(max_workers=3) as _news_exec:
            batches = list(_news_exec.map(
                lambda qh: self._fetch_from_newsapi(qh[0], qh[1]),
                [(q, h) for _, q, h in queries],
            ))
        for (label, _q, _h), batch in zip(queries, batches):
            new_count = 0
            for item in batch:
                if item.url and item.url not in seen_urls:
                    seen_urls.add(item.url)
                    all_items.append(item)
                    new_count += 1
            if new_count:
                logger.debug(f"NewsAPI {label}: {new_count} 条")
        return [
            i for i in all_items
            if any(w in (i.title + " " + i.summary).lower() for w in _IMPACT_WORDS)
        ]

    def _fetch_anysearch_multi(self, hours: int, max_results: int) -> list[NewsItem]:
        """anysearch 多主题并行查询 (黄金/宏观/地缘/贸易).

        2026-08-25 重写: 旧单查询 'gold price' 是图表页 SEO 词, 返回价格页而非新闻;
        且 NewsAPI 短路使 f1de712 加的贸易主题仍未真正生效. 现主路径总是执行,
        贸易/地缘主题放宽 freshness 到 week(72h 覆盖跨周末大事件).
        """
        today = datetime.now()
        is_nfp_day = today.weekday() == 4 and 1 <= today.day <= 7
        day_freshness = "day" if hours <= 24 else "week"
        # (标签, 查询词, freshness) — 贸易/地缘放宽到 week
        themes: list[tuple[str, str, str]] = [
            ("黄金", "gold price news today", day_freshness),
            ("宏观", "Fed inflation rate decision", day_freshness),
            ("地缘", "Iran Middle East sanctions", "week"),
            ("贸易", "US Canada trade tariffs trade war", "week"),
        ]
        if is_nfp_day:
            themes.append(("非农", "nonfarm payrolls results", day_freshness))

        def _search_one(t: tuple[str, str, str]) -> list[NewsItem]:
            return self.anysearch.search(
                query=t[1], max_results=max_results,
                freshness=t[2], content_types=["news"],
            )

        with ThreadPoolExecutor(max_workers=len(themes)) as ex:
            batches = list(ex.map(_search_one, themes))

        merged: list[NewsItem] = []
        seen: set[str] = set()
        for (label, _q, _f), batch in zip(themes, batches):
            new_count = 0
            for it in batch:
                # 去重: URL 优先, 无 URL 用标题
                key = it.url or (it.title or "").strip().lower()[:80]
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                merged.append(it)
                new_count += 1
            if new_count:
                logger.debug(f"anysearch {label}: {new_count} 条")

        # 过滤噪音条目 (短标题/URL 标题; 结构行已在解析层剔除)
        return [
            i for i in merged
            if len(i.title) > 20 and not i.title.startswith("http")
        ]

    def _fetch_from_newsapi(self, query: str, hours: int) -> list[NewsItem]:
        """从 NewsAPI 获取新闻（带重试）."""
        from_date = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d")
        params = {
            "q": query,
            "from": from_date,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": 20,
            "apiKey": self.newsapi_key,
        }

        last_error: Exception | None = None
        # 重试上限 1 (fast-analysis 2026-08-21: proxy_required 已让 mihomo 失败快速失败,
        # 再重试一次只是叠加等待; 磁盘缓存兜底已有 30min TTL)
        for attempt in range(1):
            try:
                # newsapi.org 国内必须走 mihomo 代理: proxy_required=True 使 mihomo 失败即快速失败,
                # 不叠 direct/curl/sys/node 多层回退 (每层各吃一个 timeout 会拖慢 pipeline)
                response = fallback_get(
                    "https://newsapi.org/v2/everything", params=params,
                    timeout=8, proxy_required=True,
                )
                response.raise_for_status()
                data = response.json()

                if data.get("status") != "ok":
                    logger.warning(f"NewsAPI错误: {data.get('message')}")
                    return []

                items: list[NewsItem] = []
                for article in data.get("articles", []):
                    published_at_str = article.get("publishedAt", "")
                    try:
                        published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                    except ValueError:
                        published_at = datetime.now()

                    item = NewsItem(
                        title=article.get("title", ""),
                        source=article.get("source", {}).get("name", "Unknown"),
                        published_at=published_at,
                        url=article.get("url", ""),
                        summary=article.get("description", ""),
                    )
                    item.metadata["source_tier"] = get_source_tier(
                        article.get("source", {}).get("name", "Unknown"),
                        article.get("url", ""),
                    )
                    items.append(item)

                return items
            except Exception as e:
                last_error = e
                # 401/403 等认证错误不重试
                if isinstance(e, httpx.HTTPStatusError) and not _should_retry_status(e.response.status_code):
                    break
                if not _is_retryable_error(e) and not isinstance(e, httpx.HTTPStatusError):
                    break
                # 单次尝试, 失败不再退避重试 (fast-analysis: 磁盘缓存兜底)
                logger.warning(f"NewsAPI请求失败 (尝试 {attempt + 1}/1): {e}")

        logger.warning(f"NewsAPI请求最终失败: {last_error}")
        return []

    def fetch_breaking(self) -> list[NewsItem]:
        """抓取突发新闻."""
        items = self.fetch_latest(hours=6)
        return [item for item in items if item.is_breaking]

    def analyze_sentiment(self, items: list[NewsItem]) -> list[NewsItem]:
        """基于关键词对新闻做简单情感分析."""
        for item in items:
            text = f"{item.title} {item.summary}".lower()

            bull_count = sum(1 for kw in self.BULLISH_KEYWORDS if kw.lower() in text)
            bear_count = sum(1 for kw in self.BEARISH_KEYWORDS if kw.lower() in text)
            break_count = sum(1 for kw in self.BREAKING_KEYWORDS if kw.lower() in text)

            total = bull_count + bear_count
            if total > 0:
                item.sentiment = (bull_count - bear_count) / max(total, 3)
            else:
                item.sentiment = 0.0

            item.is_breaking = break_count >= 2 or any(
                kw.lower() in text for kw in ["breaking", "突发", "urgent"]
            )

            item.keywords = [
                kw for kw in self.BULLISH_KEYWORDS + self.BEARISH_KEYWORDS + self.BREAKING_KEYWORDS
                if kw.lower() in text
            ][:5]

        return items

    def detect_anomaly(self, items: list[NewsItem]) -> list[NewsItem]:
        """检测异常信号 — 单一信源集中报道."""
        if not items:
            return []

        from collections import Counter
        sources = [item.source for item in items]
        source_counts = Counter(sources)

        return [item for item in items if source_counts[item.source] > len(items) * 0.5]
