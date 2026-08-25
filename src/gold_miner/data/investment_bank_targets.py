"""投行黄金目标价监控 — 追踪主流投行金价预测变化.

监控投行:
- Goldman Sachs (高盛)
- Morgan Stanley (摩根士丹利)
- JPMorgan (摩根大通)
- UBS (瑞银)
- Citigroup (花旗)
- Bank of America (美银)
- Deutsche Bank (德意志银行)
- Credit Suisse (瑞信/瑞银)

信号逻辑:
- 多家投行同时上调目标价 → 机构共识看涨
- 投行目标价 vs 现货价格溢价率 → 上涨空间估计
- 目标价连续下调 → 机构信心下降
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from gold_miner.data.caching import DiskCache, TtlCache
from gold_miner.proxy import get_proxied_client


@dataclass
class PriceTarget:
    """单投行目标价记录."""

    bank: str
    target_price: float  # USD/oz
    current_price: float  # 录入时的现货价
    rating: str = ""  # buy/hold/sell
    date: datetime = field(default_factory=datetime.now)
    horizon: str = "12m"  # 目标价时间 horizon
    as_of_price: float = 0.0  # 目标价录入时的基准现货价 (时效判断用; 0=未知/无法判断)

    @property
    def upside_pct(self) -> float:
        """目标价相对当前现货的上涨空间."""
        if self.current_price <= 0:
            return 0.0
        return (self.target_price / self.current_price - 1) * 100

    @property
    def is_bullish(self) -> bool:
        return self.upside_pct > 5

    @property
    def is_bearish(self) -> bool:
        return self.upside_pct < -5


class InvestmentBankTargetFetcher:
    """投行目标价获取器.

    数据来源策略:
    1. 搜索引擎抓取最新投行研报摘要
    2. 回退到已知最新目标价数据库
    """

    # 监控投行列表
    BANKS: list[dict[str, Any]] = [
        {"name": "Goldman Sachs", "alias": ["高盛", "Goldman"], "weight": 1.5},
        {"name": "Morgan Stanley", "alias": ["摩根士丹利", "Morgan Stanley"], "weight": 1.4},
        {"name": "JPMorgan", "alias": ["摩根大通", "JPM"], "weight": 1.4},
        {"name": "UBS", "alias": ["瑞银", "UBS"], "weight": 1.3},
        {"name": "Citigroup", "alias": ["花旗", "Citi"], "weight": 1.2},
        {"name": "Bank of America", "alias": ["美银", "BofA"], "weight": 1.2},
        {"name": "Deutsche Bank", "alias": ["德意志银行", "DB"], "weight": 1.1},
        {"name": "Wells Fargo", "alias": ["富国银行"], "weight": 1.0},
        {"name": "Barclays", "alias": ["巴克莱"], "weight": 1.0},
        {"name": "HSBC", "alias": ["汇丰"], "weight": 1.0},
    ]

    # 回退数据 — 基于2026年公开信息的近似目标价
    FALLBACK_TARGETS: list[PriceTarget] = field(default_factory=lambda: [
        PriceTarget("Goldman Sachs", 3700, 3300, "Buy", datetime(2026, 5, 15), as_of_price=3300),
        PriceTarget("Morgan Stanley", 3600, 3300, "Overweight", datetime(2026, 5, 10), as_of_price=3300),
        PriceTarget("JPMorgan", 3500, 3300, "Overweight", datetime(2026, 5, 12), as_of_price=3300),
        PriceTarget("UBS", 3800, 3300, "Buy", datetime(2026, 5, 8), as_of_price=3300),
        PriceTarget("Citigroup", 3400, 3300, "Neutral", datetime(2026, 5, 5), as_of_price=3300),
        PriceTarget("Bank of America", 3650, 3300, "Buy", datetime(2026, 5, 18), as_of_price=3300),
        PriceTarget("Deutsche Bank", 3550, 3300, "Buy", datetime(2026, 5, 3), as_of_price=3300),
    ])

    def __init__(self) -> None:
        self._fallback = [
            PriceTarget("Goldman Sachs", 3700, 3300, "Buy", datetime(2026, 5, 15), as_of_price=3300),
            PriceTarget("Morgan Stanley", 3600, 3300, "Overweight", datetime(2026, 5, 10), as_of_price=3300),
            PriceTarget("JPMorgan", 3500, 3300, "Overweight", datetime(2026, 5, 12), as_of_price=3300),
            PriceTarget("UBS", 3800, 3300, "Buy", datetime(2026, 5, 8), as_of_price=3300),
            PriceTarget("Citigroup", 3400, 3300, "Neutral", datetime(2026, 5, 5), as_of_price=3300),
            PriceTarget("Bank of America", 3650, 3300, "Buy", datetime(2026, 5, 18), as_of_price=3300),
            PriceTarget("Deutsche Bank", 3550, 3300, "Buy", datetime(2026, 5, 3), as_of_price=3300),
        ]
        # 双层缓存: 进程内 TtlCache + 跨进程 DiskCache (投行目标价低频, 24h).
        # 搜索引擎抓取是最慢环节 (3 个串行 Bing 查询), scan 中
        # _bank_target_signals 与 _composite_smart_money_signal 会重复调用,
        # 且每次 scan 都是新进程 — 磁盘缓存让后续 scan 直接读文件跳过 Bing.
        self._search_cache = TtlCache(ttl_seconds=600)
        self._bank_disk = DiskCache(key="bank_targets", ttl_seconds=86400)

    def fetch_all_targets(self, current_spot: float = 3300) -> list[PriceTarget]:
        """获取所有投行最新目标价.

        Args:
            current_spot: 当前现货黄金价格 (USD/oz)
        """
        try:
            # 尝试从搜索引擎获取最新目标价 (600s 内缓存命中不重复搜索)
            web_targets = self._fetch_from_search_cached(current_spot)
            if web_targets:
                # 重新绑定当前现货价 (缓存中 target_price 固定, current_price 按调用时点刷新)
                for t in web_targets:
                    t.current_price = current_spot
                return web_targets
        except Exception as e:
            logger.debug(f"投行目标价搜索失败: {e}")

        # 回退: 更新回退数据中的当前价格
        for t in self._fallback:
            t.current_price = current_spot
        return self._fallback

    def _fetch_from_search_cached(self, current_spot: float = 3300) -> list[PriceTarget] | None:
        """缓存版本 — 进程内 TtlCache + 跨进程 DiskCache(24h), 避免重复 Bing 搜索.

        DiskCache 存完整稳定字段 (bank/target_price/rating/date/as_of_price);
        current_price 由 fetch_all_targets 按调用时点刷新为现价。
        """
        targets = self._search_cache.get()
        if targets is not None:
            # 返回拷贝, 避免调用方修改共享缓存对象 (current_price 会被 fetch_all_targets 改写)
            return [copy.copy(t) for t in targets]
        disk = self._bank_disk.get()
        if disk:
            targets = [
                # current_price 用占位值, fetch_all_targets 返回前会按调用时点统一刷新
                PriceTarget(
                    bank=d["bank"],
                    target_price=d["target_price"],
                    current_price=0.0,
                    rating=d.get("rating", ""),
                    date=datetime.fromisoformat(d["date"]) if d.get("date") else datetime.now(),
                    as_of_price=float(d.get("as_of_price", 0) or 0),
                )
                for d in disk
            ]
            self._search_cache.set(targets)
            return [copy.copy(t) for t in targets]
        result = self._fetch_from_search(current_spot)
        if result:
            self._search_cache.set(result)
            self._bank_disk.set(
                [
                    {
                        "bank": t.bank,
                        "target_price": t.target_price,
                        "rating": t.rating,
                        "date": t.date.isoformat() if t.date else None,
                        "as_of_price": t.as_of_price,
                    }
                    for t in result
                ]
            )
        return result

    def fetch_consensus(self, current_spot: float = 3300) -> dict[str, Any]:
        """获取投行共识摘要.

        Returns:
            dict with: avg_target, median_target, upside_pct,
                       bullish_count, bearish_count, neutral_count,
                       rating_bullish/bearish/neutral_count (评级方向),
                       stale/as_of_price_avg/staleness_ratio (目标价时效),
                       latest_change_bank, latest_change_direction
        """
        targets = self.fetch_all_targets(current_spot)
        if not targets:
            return {"status": "no_data"}

        prices = [t.target_price for t in targets]
        avg_price = sum(prices) / len(prices)
        median_price = sorted(prices)[len(prices) // 2]

        bullish = sum(1 for t in targets if t.is_bullish)
        bearish = sum(1 for t in targets if t.is_bearish)
        neutral = len(targets) - bullish - bearish

        upside = (avg_price / current_spot - 1) * 100 if current_spot > 0 else 0

        # 评级方向 (2026-08-26): 投行「评级」是当前立场主信号, 目标价相对现价仅作空间参考。
        # 金价快速上涨时投行研报目标价更新慢, 目标价会滞后现价 → 按 upside 判多空会误判
        # (事故: 2026-08-25 目标价停在 $3300 基准, 现价 $4635, 7家评级全 Buy/Overweight 却被判「共识看空」)。
        _BULLISH_RATINGS = {
            "buy", "overweight", "strong buy", "outperform", "add", "accumulate", "top pick",
        }
        _BEARISH_RATINGS = {
            "sell", "underweight", "reduce", "underperform", "neutral-weight", "avoid",
        }
        rating_bullish = sum(1 for t in targets if t.rating.strip().lower() in _BULLISH_RATINGS)
        rating_bearish = sum(1 for t in targets if t.rating.strip().lower() in _BEARISH_RATINGS)
        rating_neutral = len(targets) - rating_bullish - rating_bearish

        # 目标价时效: 基准价(as_of_price) vs 现价偏离 >15% → 目标价滞后现价 (陈旧)
        as_of_prices = [t.as_of_price for t in targets if t.as_of_price > 0]
        as_of_avg = sum(as_of_prices) / len(as_of_prices) if as_of_prices else current_spot
        staleness_ratio = (current_spot - as_of_avg) / as_of_avg if as_of_avg > 0 else 0.0
        stale = abs(staleness_ratio) > 0.15

        return {
            "status": "ok",
            "avg_target": round(avg_price, 0),
            "median_target": round(median_price, 0),
            "upside_pct": round(upside, 1),
            "bullish_count": bullish,
            "bearish_count": bearish,
            "neutral_count": neutral,
            "total_banks": len(targets),
            "highest": max(targets, key=lambda t: t.target_price).bank,
            "highest_target": max(prices),
            "lowest": min(targets, key=lambda t: t.target_price).bank,
            "lowest_target": min(prices),
            # 时效保护 (2026-08-26)
            "rating_bullish_count": rating_bullish,
            "rating_bearish_count": rating_bearish,
            "rating_neutral_count": rating_neutral,
            "as_of_price_avg": round(as_of_avg, 0),
            "staleness_ratio": round(staleness_ratio, 3),
            "stale": stale,
        }

    def _fetch_from_search(self, current_spot: float) -> list[PriceTarget] | None:
        """通过搜索引擎获取最新投行目标价."""
        # 搜索关键词策略
        queries = [
            "Goldman Sachs gold price target 2026",
            "Morgan Stanley gold forecast 2026",
            "JPMorgan gold price prediction",
        ]

        targets: list[PriceTarget] = []
        for query in queries:
            try:
                results = self._search_bing(query)
                parsed = self._parse_target_from_text(results, current_spot)
                targets.extend(parsed)
            except Exception:
                continue

        return targets if targets else None

    def _search_bing(self, query: str) -> str:
        """Bing搜索."""
        url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        }
        try:
            with get_proxied_client(timeout=15, follow_redirects=True) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.text
        except Exception:
            return ""

    def _parse_target_from_text(self, html: str, current_spot: float) -> list[PriceTarget]:
        """从搜索结果HTML解析目标价."""
        targets: list[PriceTarget] = []
        # 简单正则匹配: 银行名 + $数字 + gold
        for bank_info in self.BANKS:
            bank_name = bank_info["name"]
            # 匹配 "$3,500" 或 "$3500"; 必须带 $ 前缀, 避免把 "2026" 等裸年份数字当目标价
            pattern = rf"{re.escape(bank_name)}.*?\$([\d,]+)[\s\D]{{0,30}}gold"
            matches = re.findall(pattern, html, re.IGNORECASE)
            for m in matches:
                try:
                    price = float(m.replace(",", ""))
                    if 1900 < price < 2100:  # 排除年份区间误抓 (如 "2026")
                        continue
                    if 2000 < price < 10000:
                        targets.append(PriceTarget(
                            bank=bank_name,
                            target_price=price,
                            current_price=current_spot,
                            date=datetime.now(),
                            as_of_price=current_spot,
                        ))
                except ValueError:
                    continue
        return targets

    def get_bullish_score(self, current_spot: float = 3300) -> float:
        """计算投行共识看涨分数 (-1 ~ +1)."""
        consensus = self.fetch_consensus(current_spot)
        if consensus.get("status") != "ok":
            return 0.0

        total = consensus["total_banks"]
        if total == 0:
            return 0.0

        bullish = consensus["bullish_count"]
        bearish = consensus["bearish_count"]
        return (bullish - bearish) / total
