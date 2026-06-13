"""新闻数据抓取 — 多源聚合: NewsAPI / anysearch / 搜索引擎."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from gold_miner.config import settings
from gold_miner.proxy import get_proxied_client


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

        try:
            with get_proxied_client(timeout=30) as client:
                resp = client.post(self.ENDPOINT, json=payload, headers=headers)
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

            return self._parse_anysearch_results(text)
        except Exception as e:
            logger.warning(f"anysearch请求失败: {e}")
            return []

    def _parse_anysearch_results(self, text: str) -> list[NewsItem]:
        """解析 anysearch 返回的文本结果."""
        items: list[NewsItem] = []
        if not text:
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
            # 非 JSON，尝试按行解析
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            for line in lines:
                if line.startswith(("http://", "https://")):
                    items.append(NewsItem(title=line, source="anysearch", published_at=datetime.now(), url=line))
                elif len(line) > 20:
                    items.append(NewsItem(title=line, source="anysearch", published_at=datetime.now()))

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

        return NewsItem(
            title=entry.get("title", entry.get("name", "")),
            source=entry.get("source", entry.get("domain", "anysearch")),
            published_at=published_at,
            url=entry.get("url", entry.get("link", "")),
            summary=entry.get("summary", entry.get("snippet", entry.get("description", ""))),
        )


class SearchEngineFetcher:
    """使用搜索引擎直接抓取新闻 — 无需 API key."""

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def fetch_from_duckduckgo(self, query: str, max_results: int = 10) -> list[NewsItem]:
        """从 DuckDuckGo 抓取搜索结果."""
        url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
        try:
            with get_proxied_client(timeout=30, follow_redirects=True) as client:
                resp = client.get(url, headers=self._HEADERS)
            resp.raise_for_status()
            return self._parse_duckduckgo_html(resp.text, max_results)
        except Exception as e:
            logger.warning(f"DuckDuckGo抓取失败: {e}")
            return []

    def fetch_from_bing(self, query: str, max_results: int = 10) -> list[NewsItem]:
        """从 Bing 抓取搜索结果."""
        url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
        try:
            with get_proxied_client(timeout=30, follow_redirects=True) as client:
                resp = client.get(url, headers=self._HEADERS)
            resp.raise_for_status()
            return self._parse_bing_html(resp.text, max_results)
        except Exception as e:
            logger.warning(f"Bing抓取失败: {e}")
            return []

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

            items.append(NewsItem(
                title=title,
                source="DuckDuckGo",
                published_at=datetime.now(),
                url=url,
                summary=summary,
            ))

        return items

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

            items.append(NewsItem(
                title=title,
                source="Bing",
                published_at=datetime.now(),
                url=url,
                summary=summary,
            ))

        return items


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
    ]

    def __init__(self) -> None:
        self.newsapi_key = settings.news_api_key
        self.anysearch = AnySearchFetcher()
        self.search_engine = SearchEngineFetcher()
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = get_proxied_client(timeout=30)
        return self._client

    def fetch_latest(
        self,
        query: str = "gold OR XAU OR FED OR 美联储 OR 黄金",
        hours: int = 24,
        max_results: int = 10,
    ) -> list[NewsItem]:
        """抓取最新新闻 — 多源回退.

        依次尝试 NewsAPI → anysearch → 搜索引擎，任一成功即返回.
        """
        items: list[NewsItem] = []

        # 检测是否非农发布日（每月第一个周五）
        from datetime import datetime as dt
        today = dt.now()
        is_nfp_day = today.weekday() == 4 and 1 <= today.day <= 7

        # 1. NewsAPI (国内直连可用, 质量最高)
        if self.newsapi_key:
            nfp_query = "nonfarm payrolls May 2026 results" if is_nfp_day else "nonfarm payrolls"

            # 多批查询: 黄金 + 宏观/地缘 + 就业
            queries = [
                ("gold", "gold price OR gold market OR gold forecast"),
                ("宏观", f"{nfp_query} OR Fed rate decision OR CPI inflation OR unemployment"),
                ("地缘", "Iran conflict OR Middle East war OR geopolitical crisis"),
            ]
            all_items: list[NewsItem] = []
            seen_urls: set[str] = set()

            for label, q in queries:
                batch = self._fetch_from_newsapi(q, hours)
                new_count = 0
                for item in batch:
                    if item.url and item.url not in seen_urls:
                        seen_urls.add(item.url)
                        all_items.append(item)
                        new_count += 1
                if new_count:
                    logger.debug(f"NewsAPI {label}: {new_count} 条")

            # 宽松过滤: 保留可能影响金价的新闻
            impact_words = [
                "gold", "xau", "bullion", "precious metal",
                "fed", "rate", "inflation", "cpi", "ppi",
                "payroll", "nfp", "nonfarm", "unemployment", "job",
                "iran", "middle east", "war", "conflict", "geopolitical",
                "central bank", "stimulus", "recession", "dollar", "treasury",
                "tariff", "sanction", "crisis", "safe haven", "避险",
                "silver", "metal", "commodity", "precious",
            ]
            items = [
                i for i in all_items
                if any(w in (i.title + " " + i.summary).lower() for w in impact_words)
            ]

            if items:
                logger.info(f"NewsAPI 返回 {len(items)} 条新闻")
                return items

        # 2. anysearch + NFP专项（每月第一个周五自动补充）
        if is_nfp_day:
            nfp_items = self.anysearch.search(
                query="nonfarm payrolls May results 2026",
                max_results=3, freshness="day", content_types=["news"],
            )
            if nfp_items:
                nfp_items = [i for i in nfp_items if len(i.title) > 20]
                logger.info(f"anysearch NFP专项: {len(nfp_items)} 条")

        items = self.anysearch.search(
            query="gold price",
            max_results=max_results,
            freshness="day" if hours <= 24 else "week",
            content_types=["news"],
        )
        if items:
            # 过滤噪音行 (URL-only 条目)
            items = [i for i in items if len(i.title) > 20 and not i.title.startswith("http")]
            if items:
                logger.info(f"anysearch 返回 {len(items)} 条新闻")
                return items

        # 3. DuckDuckGo
        items = self.search_engine.fetch_from_duckduckgo("gold price news", max_results)
        if items:
            logger.info(f"DuckDuckGo 返回 {len(items)} 条新闻")
            return items

        # 4. Bing 兜底
        items = self.search_engine.fetch_from_bing("gold price news", max_results)
        if items:
            logger.info(f"Bing 返回 {len(items)} 条新闻")
            return items

        logger.warning("所有新闻源均无法获取数据")
        return []

    def _fetch_from_newsapi(self, query: str, hours: int) -> list[NewsItem]:
        """从 NewsAPI 获取新闻."""
        try:
            from_date = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d")
            response = self.client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "from": from_date,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 20,
                    "apiKey": self.newsapi_key,
                },
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
                items.append(item)

            return items
        except Exception as e:
            logger.warning(f"NewsAPI请求失败: {e}")
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
