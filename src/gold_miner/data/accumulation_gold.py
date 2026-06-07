"""积存金数据抓取 -- 上海黄金交易所 / AKShare."""

from datetime import datetime, timedelta
from time import sleep
from typing import Any

import akshare as ak
import httpx
import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.proxy import get_proxied_client

# 金衡盎司 → 克
_OZ_TO_GRAM = 31.1035
_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class AccumulationGoldFetcher(DataFetcher):
    """积存金数据获取器 -- 人民币计价.

    积存金定价紧密跟随上海黄金交易所 Au99.99 现货合约,
    同时参考国际金价 (XAU/USD) 与美元/人民币汇率计算溢价.
    """

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="accumulation_gold",
                source="AKShare / SGE",
                frequency="hour",
                description="积存金 人民币/克",
            )
        )

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        days: int = 120,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """抓取积存金历史行情数据.

        积存金暂无可直接调用的专用 AKShare API,
        使用上海黄金交易所 Au99.99 现货价格作为替代,
        因为积存金定价紧密锚定 Au99.99.

        Args:
            start: 起始时间, 默认 days 天前
            end: 结束时间, 默认现在
            days: 回溯天数, 当 start 未提供时使用
        """
        end = end or datetime.now()
        start = start or (end - timedelta(days=days))

        return self._fetch_from_akshare(start, end)

    def _fetch_from_akshare(
        self, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """从 AKShare 获取上海金交所 Au99.99 历史数据作为积存金价格."""
        try:
            df = ak.spot_hist_sge(symbol="Au99.99")
            if df.empty:
                logger.warning("AKShare spot_hist_sge 返回空数据")
                return pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )

            # 列名标准化
            df = df.rename(
                columns={
                    "date": "timestamp",
                    "open": "open",
                    "close": "close",
                    "low": "low",
                    "high": "high",
                }
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["volume"] = 0.0  # AKShare 不提供成交量

            # 按时间过滤
            df = df[(df["timestamp"] >= start) & (df["timestamp"] <= end)]
            df = df.sort_values("timestamp").reset_index(drop=True)

            return self.validate(
                df[["timestamp", "open", "high", "low", "close", "volume"]]
            )
        except Exception as e:
            logger.warning(f"AKShare 积存金数据获取失败: {e}")
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最近 5 天积存金数据."""
        return self.fetch(days=5)

    def fetch_premium(
        self, spot_usd_cny: float | None = None
    ) -> dict[str, Any]:
        """计算积存金相对现货黄金等值的溢价/折价.

        积存金 (Au99.99, CNY/g) 与 国际金价 (XAU/USD, USD/oz)
        的价差可反映国内市场情绪与供需.

        Args:
            spot_usd_cny: 美元/人民币汇率, 省略时自动获取

        Returns:
            dict: premium_pct / spot_cny_equivalent / accumulation_price / timestamp
        """
        try:
            # 获取最新 SGE Au99.99 价格
            df = self.fetch_latest()
            if df.empty:
                return {
                    "premium_pct": 0.0,
                    "spot_cny_equivalent": 0.0,
                    "accumulation_price": 0.0,
                    "timestamp": datetime.now(),
                }

            accum_price = float(df["close"].iloc[-1])

            # 获取汇率
            rate = spot_usd_cny if spot_usd_cny else self._fetch_usd_cny_rate()

            # 获取国际金价 (XAU/USD)
            xau_price = self._fetch_xau_usd()
            if xau_price is None:
                return {
                    "premium_pct": 0.0,
                    "spot_cny_equivalent": 0.0,
                    "accumulation_price": accum_price,
                    "timestamp": datetime.now(),
                }

            # 国际金价换算为 人民币/克
            spot_cny_equivalent = xau_price * rate / _OZ_TO_GRAM

            # 溢价 = (积存金价格 - 等值国际金价) / 等值国际金价 * 100
            if spot_cny_equivalent > 0:
                premium_pct = (accum_price - spot_cny_equivalent) / spot_cny_equivalent * 100
            else:
                premium_pct = 0.0

            return {
                "premium_pct": round(premium_pct, 4),
                "spot_cny_equivalent": round(spot_cny_equivalent, 4),
                "accumulation_price": round(accum_price, 4),
                "timestamp": datetime.now(),
            }
        except Exception as e:
            logger.warning(f"积存金溢价计算失败: {e}")
            return {
                "premium_pct": 0.0,
                "spot_cny_equivalent": 0.0,
                "accumulation_price": 0.0,
                "timestamp": datetime.now(),
            }

    def _fetch_usd_cny_rate(self) -> float:
        """获取美元/人民币汇率. 失败时返回 7.2."""
        try:
            df = ak.currency_boc_safe()
            if not df.empty:
                usd_row = df[df["货币"] == "美元"]
                if not usd_row.empty:
                    return float(usd_row["现汇买入价"].iloc[0])
        except Exception as e:
            logger.warning(f"汇率获取失败, 使用默认值 7.2: {e}")
        return 7.2

    def _fetch_xau_usd(self) -> float | None:
        """获取国际金价 XAU/USD (美元/盎司) 从 Yahoo Finance."""
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%3DXAUUSD?interval=1d&range=5d"

        for attempt in range(3):
            try:
                with get_proxied_client(timeout=30) as client:
                    resp = client.get(url, headers=_YAHOO_HEADERS)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except Exception as e:
                logger.warning(f"Yahoo Finance XAU/USD 请求失败 (尝试 {attempt + 1}/3): {e}")
                if attempt < 2:
                    sleep(2**attempt)
                else:
                    return None

        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return None

        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is not None:
            return float(price)

        # 从报价数据中取最新收盘价
        quotes = result.get("indicators", {}).get("quote", [{}])[0]
        closes = quotes.get("close", [])
        if closes:
            return float(closes[-1])

        return None
