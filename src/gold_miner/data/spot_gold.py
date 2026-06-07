"""现货黄金数据 — 上海金交所 Au99.99.

历史日线 (OHLCV): AKShare spot_hist_sge
实时报价: jinjia.com.cn (静态HTML, 3分钟更新)
备用: Yahoo Finance XAU/USD
"""

from datetime import datetime, timedelta
from time import sleep
from typing import Any

import akshare as ak
import httpx
import pandas as pd
from bs4 import BeautifulSoup
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.proxy import get_proxied_client

JINJIA_URL = "https://www.jinjia.com.cn/"
JINJIA_INTL_URL = "https://www.jinjia.com.cn/gjgold/"
_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


class SpotGoldFetcher(DataFetcher):
    """现货黄金数据 — 上海金交所 Au99.99 人民币/克.

    历史日线 (OHLCV): AKShare spot_hist_sge → SGE 官方收盘价
    实时报价: jinjia.com.cn → 静态HTML, ~3分钟延迟
    备用: Yahoo Finance XAU/USD
    """

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="spot_gold",
                source="上海黄金交易所 + jinjia.com.cn",
                frequency="daily + realtime",
                description="现货黄金 Au99.99 人民币/克",
            )
        )
        self.symbol = settings.yahoo_symbol_spot

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        interval: str = "1d",
        days: int = 30,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取历史 OHLCV 日线数据."""
        end = end or datetime.now()
        start = start or (end - timedelta(days=days))

        df = self._fetch_from_akshare(start, end)
        if not df.empty:
            return df

        logger.warning("AKShare 不可用，回退到 Yahoo Finance")
        return self._fetch_from_yahoo(start, end, interval)

    def fetch_latest(self) -> pd.DataFrame:
        return self.fetch(days=5)

    def fetch_realtime_quote(self) -> dict[str, Any]:
        """获取实时报价 — jinjia.com.cn (国内+国际)."""
        domestic = self._fetch_jinjia_quote()
        international = self._fetch_jinjia_international()

        result: dict[str, Any] = {
            "symbol": "AU9999 (SGE)",
            "source": "jinjia.com.cn",
            "unit_domestic": "人民币/克",
            "unit_international": "美元/盎司",
            "timestamp": datetime.now(),
        }

        if domestic:
            result["domestic_price"] = domestic["last_price"]
            result["domestic_change_pct"] = domestic.get("change_pct")
        if international:
            result["international"] = international

        if not domestic and not international:
            try:
                df = ak.spot_hist_sge(symbol="Au99.99")
                if not df.empty:
                    result["domestic_price"] = float(df["close"].iloc[-1])
                    result["source"] = "上海黄金交易所 (最新收盘)"
            except Exception:
                pass

        return result

    def fetch_international_quote(self) -> dict[str, Any] | None:
        """获取国际金价实时报价."""
        return self._fetch_jinjia_international()
        try:
            df = ak.spot_hist_sge(symbol="Au99.99")
            if not df.empty:
                return {
                    "symbol": "Au99.99 (SGE)",
                    "last_price": float(df["close"].iloc[-1]),
                    "date": str(df["date"].iloc[-1]),
                    "source": "上海黄金交易所 (最新收盘)",
                    "unit": "人民币/克",
                    "timestamp": datetime.now(),
                }
        except Exception:
            pass

        return {"symbol": "Au99.99", "error": "数据不可用", "timestamp": datetime.now()}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _fetch_from_akshare(
        self, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """AKShare 上海金交所 Au99.99 历史日线 + jinjia 实时补充."""
        try:
            df = ak.spot_hist_sge(symbol="Au99.99")
            if df.empty:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

            df = df.rename(
                columns={
                    "date": "timestamp", "open": "open", "close": "close",
                    "low": "low", "high": "high",
                }
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["volume"] = 0.0

            # 补充今日实时价 (AKShare只有T-1收盘)
            today = datetime.now()
            if df["timestamp"].max().date() < today.date():
                live = self._fetch_jinjia_quote()
                if live and live.get("last_price"):
                    today_row = pd.DataFrame([{
                        "timestamp": end,
                        "open": live["last_price"],
                        "high": live["last_price"],
                        "low": live["last_price"],
                        "close": live["last_price"],
                        "volume": 0.0,
                    }])
                    df = pd.concat([df, today_row], ignore_index=True)
                    logger.info(f"今日实时价: {live['last_price']:.2f} (来源: {live['source']})")

            df = df[(df["timestamp"] >= pd.Timestamp(start)) &
                    (df["timestamp"] <= pd.Timestamp(end))]
            df = df.sort_values("timestamp").reset_index(drop=True)

            return self.validate(df[["timestamp", "open", "high", "low", "close", "volume"]])
        except Exception as e:
            logger.warning(f"AKShare 数据获取失败: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    @staticmethod
    def _fetch_jinjia_quote() -> dict[str, Any] | None:
        """从 jinjia.com.cn 获取 AU9999 实时报价 (静态HTML, ~3分钟延迟)."""
        try:
            resp = httpx.get(
                JINJIA_URL, headers=_WEB_HEADERS, timeout=10, follow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text

            soup = BeautifulSoup(html, "html.parser")
            # jinjia.com.cn 使用 <li> + <div> 列表结构
            for li in soup.find_all("li"):
                name_div = li.find("div", class_="name")
                if not name_div:
                    continue
                name = name_div.get_text(strip=True)
                if "AU9999" not in name and "Au9999" not in name:
                    continue

                new_div = li.find("div", class_="new")
                rise_div = li.find("div", class_="rise")
                if not new_div:
                    continue

                price_text = new_div.get_text(strip=True)
                change_text = rise_div.get_text(strip=True) if rise_div else ""
                # 找更新时间
                update_div = li.find("div", class_=lambda c: c and "update" in str(c).lower() if c else False)
                update_text = update_div.get_text(strip=True) if update_div else ""

                try:
                    price = float(price_text)
                    if 300 < price < 1500:
                        change_pct = None
                        if change_text:
                            try:
                                change_pct = float(change_text.replace("%", "").replace("+", "")) / 100
                            except ValueError:
                                pass
                        return {
                            "symbol": "AU9999 (SGE)",
                            "last_price": price,
                            "change_pct": change_pct,
                            "source": "jinjia.com.cn",
                            "unit": "人民币/克",
                            "update_time": update_text,
                            "timestamp": datetime.now(),
                        }
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    def _fetch_from_yahoo(
        self, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame:
        """Yahoo Finance XAU/USD (备用)."""
        period1 = int(start.timestamp())
        period2 = int(end.timestamp())
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{self.symbol}"
            f"?period1={period1}&period2={period2}&interval={interval}"
        )

        for attempt in range(3):
            try:
                with get_proxied_client(timeout=30) as client:
                    resp = client.get(url, headers=_YAHOO_HEADERS)
                    resp.raise_for_status()
                    data = resp.json()
                break
            except Exception as e:
                logger.warning(f"Yahoo Finance请求失败 (尝试 {attempt + 1}/3): {e}")
                if attempt < 2:
                    sleep(2 ** attempt)
                else:
                    logger.error("Yahoo Finance数据获取失败")
                    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        result = data.get("chart", {}).get("result", [None])[0]
        if not result:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        timestamps = result.get("timestamp", [])
        quotes = result.get("indicators", {}).get("quote", [{}])[0]

        df = pd.DataFrame({
            "timestamp": [datetime.fromtimestamp(ts) for ts in timestamps],
            "open": quotes.get("open", []),
            "high": quotes.get("high", []),
            "low": quotes.get("low", []),
            "close": quotes.get("close", []),
            "volume": quotes.get("volume", []),
        })
        return self.validate(df)

    @staticmethod
    def _fetch_jinjia_international() -> list[dict[str, Any]] | None:
        """从 jinjia.com.cn/gjgold/ 获取国际金价实时行情."""
        try:
            resp = httpx.get(
                JINJIA_INTL_URL, headers=_WEB_HEADERS, timeout=10, follow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text

            soup = BeautifulSoup(html, "html.parser")
            results: list[dict[str, Any]] = []

            for li in soup.find_all("li"):
                name_div = li.find("div", class_="name")
                if not name_div:
                    continue
                name = name_div.get_text(strip=True)
                if not name:
                    continue

                new_div = li.find("div", class_="new")
                rise_div = li.find("div", class_="rise")
                if not new_div:
                    continue

                price_text = new_div.get_text(strip=True)
                change_text = rise_div.get_text(strip=True) if rise_div else ""

                try:
                    price = float(price_text)
                    if price <= 0:
                        continue
                    change_pct = None
                    if change_text:
                        try:
                            change_pct = float(change_text.replace("%", "").replace("+", "")) / 100
                        except ValueError:
                            pass
                    results.append({
                        "name": name,
                        "price": price,
                        "change_pct": change_pct,
                        "unit": "美元/盎司",
                    })
                except ValueError:
                    continue

            return results if results else None
        except Exception:
            pass
        return None

    def fetch_usd_cny_rate(self) -> float:
        """获取美元兑人民币汇率."""
        try:
            df = ak.currency_boc_safe()
            if not df.empty:
                usd_row = df[df["货币"] == "美元"]
                if not usd_row.empty:
                    return float(usd_row["现汇买入价"].iloc[0])
        except Exception as e:
            logger.warning(f"汇率获取失败: {e}")
        return 7.2
