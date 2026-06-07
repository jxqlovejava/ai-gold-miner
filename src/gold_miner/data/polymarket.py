"""Polymarket 预测市场数据抓取 — Gamma API.

Polymarket 是全球最大的预测市场平台，用户用真金白银押注事件结果。
与新闻舆情不同，预测市场价格反映的是"金钱激励下的真实信念"，
更难被操纵，是高质量的情绪/预期指标。

黄金相关的市场主要分为四类：
- 宏观政策：美联储利率、CPI、就业数据
- 地缘政治：战争、冲突、制裁
- 美元货币：美元指数、汇率
- 黄金直连：金价涨跌、金银比

API: https://gamma-api.polymarket.com (公开，无需认证)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.proxy import get_proxied_client

GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# 与黄金相关的关键词，按影响类别分组
# 用于客户端过滤（Gamma API 的 search 参数精度有限）
GOLD_RELATED_KEYWORDS: dict[str, list[str]] = {
    "macro": [
        "fed", "federal reserve", "rate", "interest rate",
        "inflation", "cpi", "pce", "core pce",
        "recession", "gdp", "nfp", "nonfarm", "employment",
        "jobs report", "unemployment",
    ],
    "geopolitical": [
        "war", "conflict", "middle east", "ukraine", "israel",
        "iran", "gaza", "sanction", "nuclear", "missile",
        "taiwan", "taiwan strait", "south china sea",
        "north korea", "terror", "cyberattack",
    ],
    "currency": [
        "dollar", "usd", "dxy", "us dollar", "euro", "yen",
        "japanese yen", "currency", "exchange rate",
    ],
    "gold_direct": [
        "gold", "xau", "bullion", "precious metal", "silver",
        "gold price", "spot gold", "gold above", "gold below",
    ],
    "policy": [
        "tariff", "trade war", "trump", "biden", "election",
        "debt ceiling", "government shutdown", "fiscal",
        "treasury", "bond", "yield",
    ],
}

# 同时排除与黄金无关的热门噪音市场
NOISE_KEYWORDS: list[str] = [
    "gta vi", "gta 6", "grand theft auto",
    "album", "rihanna", "kanye", "drake", "taylor swift",
    "nba", "nfl", "nhl", "super bowl", "stanley cup",
    "bitcoin", "btc", "ethereum", "eth", "crypto",
    "oscar", "grammy", "emmy", "academy award",
]


@dataclass
class PredictionMarket:
    """单个预测市场数据."""

    market_id: str
    question: str
    description: str
    outcome_yes_price: float  # 0.0 ~ 1.0
    outcome_no_price: float   # 0.0 ~ 1.0
    outcomes: list[str]
    volume_24h: float
    volume_total: float
    liquidity: float
    end_date: datetime | None
    slug: str
    condition_id: str
    updated_at: datetime
    created_at: datetime
    resolution_source: str = ""
    # 价格变化（如 API 提供）
    price_change_1w: float | None = None
    price_change_1m: float | None = None
    last_trade_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    # 关键词匹配结果
    matched_category: str = ""
    matched_keywords: list[str] = field(default_factory=list)


class PolymarketFetcher:
    """Polymarket Gamma API 采集器.

    用法:
        fetcher = PolymarketFetcher()
        markets = fetcher.fetch_gold_related(limit=200)
    """

    _HEADERS = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    def __init__(self, base_url: str = GAMMA_API_BASE) -> None:
        self.base_url = base_url
        self.keywords = GOLD_RELATED_KEYWORDS
        self.noise = NOISE_KEYWORDS

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def fetch_gold_related(
        self,
        limit: int = 200,
        min_volume_24h: float = 100.0,
        max_results: int = 30,
    ) -> list[PredictionMarket]:
        """获取与黄金相关的预测市场.

        Args:
            limit: 拉取的总市场数（越大覆盖越全）
            min_volume_24h: 24h 最低交易量过滤（剔除死市场）
            max_results: 最多返回的相关市场数
        """
        all_markets = self._fetch_active_markets(limit)
        if not all_markets:
            return []

        # 先过滤噪音
        filtered = self._filter_noise(all_markets)
        # 再筛选黄金相关
        related = self._filter_gold_related(filtered)
        # 按交易量排序，取前 N
        related.sort(key=lambda m: m.volume_24h, reverse=True)
        related = related[:max_results]

        logger.info(
            f"Polymarket: 从 {len(all_markets)} 个市场中 "
            f"过滤噪音后 {len(filtered)} 个，黄金相关 {len(related)} 个"
        )
        return related

    def fetch_market_by_id(self, market_id: str) -> PredictionMarket | None:
        """通过 condition_id 获取单个市场详情."""
        url = f"{self.base_url}/markets/{market_id}"
        try:
            data = self._get_json(url)
            if not data:
                return None
            return self._parse_market(data)
        except Exception as e:
            logger.warning(f"获取市场 {market_id} 失败: {e}")
            return None

    def fetch_macro_markets(self, limit: int = 200) -> list[PredictionMarket]:
        """仅获取宏观经济类市场（利率、通胀、就业）."""
        all_markets = self._fetch_active_markets(limit)
        filtered = self._filter_noise(all_markets)
        return [
            m for m in filtered
            if self._matches_keywords(m, self.keywords["macro"])
        ]

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _fetch_active_markets(self, limit: int) -> list[PredictionMarket]:
        """获取所有活跃市场."""
        url = f"{self.base_url}/markets?active=true&closed=false&limit={limit}"
        try:
            data = self._get_json(url)
            if not isinstance(data, list):
                logger.warning(f"Polymarket API 返回非列表: {type(data)}")
                return []
            return [self._parse_market(m) for m in data if isinstance(m, dict)]
        except Exception as e:
            logger.warning(f"Polymarket 市场列表获取失败: {e}")
            return []

    def _get_json(self, url: str) -> Any:
        """发起 HTTP GET 并返回 JSON."""
        with get_proxied_client(timeout=30) as client:
            resp = client.get(url, headers=self._HEADERS)
        resp.raise_for_status()
        return resp.json()

    def _parse_market(self, data: dict[str, Any]) -> PredictionMarket:
        """将 Gamma API 原始数据解析为 PredictionMarket."""
        # outcomePrices: ["0.72", "0.28"] 对应 outcomes: ["Yes", "No"]
        outcome_prices = data.get("outcomePrices", [])
        outcomes = data.get("outcomes", [])

        yes_price = 0.5
        no_price = 0.5
        if len(outcome_prices) >= 2:
            try:
                yes_price = float(outcome_prices[0])
                no_price = float(outcome_prices[1])
            except (ValueError, TypeError):
                pass

        # 解析日期
        end_date = self._parse_iso(data.get("endDate"))
        updated_at = self._parse_iso(data.get("updatedAt")) or datetime.now()
        created_at = self._parse_iso(data.get("createdAt")) or datetime.now()

        # 价格变化（可能不存在）
        pc_1w = self._safe_float(data.get("oneWeekPriceChange"))
        pc_1m = self._safe_float(data.get("oneMonthPriceChange"))
        last_trade = self._safe_float(data.get("lastTradePrice"))
        best_bid = self._safe_float(data.get("bestBid"))
        best_ask = self._safe_float(data.get("bestAsk"))

        return PredictionMarket(
            market_id=data.get("conditionId", data.get("id", "")),
            question=data.get("question", ""),
            description=data.get("description", ""),
            outcome_yes_price=yes_price,
            outcome_no_price=no_price,
            outcomes=outcomes if outcomes else ["Yes", "No"],
            volume_24h=float(data.get("volume24hr") or 0),
            volume_total=float(data.get("volume") or 0),
            liquidity=float(data.get("liquidity") or 0),
            end_date=end_date,
            slug=data.get("slug", ""),
            condition_id=data.get("conditionId", ""),
            updated_at=updated_at,
            created_at=created_at,
            resolution_source=data.get("resolutionSource", ""),
            price_change_1w=pc_1w,
            price_change_1m=pc_1m,
            last_trade_price=last_trade,
            best_bid=best_bid,
            best_ask=best_ask,
        )

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 过滤逻辑
    # ------------------------------------------------------------------

    def _filter_noise(self, markets: list[PredictionMarket]) -> list[PredictionMarket]:
        """排除与黄金无关的噪音市场（体育、娱乐、加密货币等）."""
        result: list[PredictionMarket] = []
        for m in markets:
            text = f"{m.question} {m.description}".lower()
            if not any(noise in text for noise in self.noise):
                result.append(m)
        return result

    def _filter_gold_related(
        self, markets: list[PredictionMarket]
    ) -> list[PredictionMarket]:
        """筛选与黄金相关的市场，并标注匹配类别."""
        result: list[PredictionMarket] = []
        for m in markets:
            text = f"{m.question} {m.description}".lower()
            matched_cats: list[str] = []
            matched_kws: list[str] = []

            for category, keywords in self.keywords.items():
                hits = [kw for kw in keywords if kw.lower() in text]
                if hits:
                    matched_cats.append(category)
                    matched_kws.extend(hits)

            if matched_cats:
                m.matched_category = matched_cats[0]  # 主类别
                m.matched_keywords = list(set(matched_kws))  # 去重
                result.append(m)

        return result

    @staticmethod
    def _matches_keywords(market: PredictionMarket, keywords: list[str]) -> bool:
        text = f"{market.question} {market.description}".lower()
        return any(kw.lower() in text for kw in keywords)
