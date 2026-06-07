"""宏观数据抓取 — 美元指数、利率、通胀."""

from datetime import datetime, timedelta
from typing import Any

import httpx
import pandas as pd
import yfinance as yf
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.proxy import get_proxied_client


class MacroDataFetcher(DataFetcher):
    """宏观数据获取器 — FRED + Yahoo Finance."""

    SERIES = {
        "dxy": "DTWEXBGS",
        "real_rate_10y": "REAINTRATREARAT10Y",
        "breakeven_10y": "T10YIE",
        "fed_rate": "DFF",
        "cpi_yoy": "CPIAUCSL",
        "ppi": "PPIACO",
        "unemployment": "UNRATE",
    }

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="macro",
                source="FRED / Yahoo Finance",
                frequency="day",
                description="美元指数、利率、通胀等宏观指标",
            )
        )
        self.api_key = settings.fred_api_key

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        series_id: str | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """从FRED获取宏观数据.

        Args:
            series_id: FRED series ID，如 'DTWEXBGS' (美元指数)
        """
        if not self.api_key:
            logger.warning("FRED API key未配置，跳过宏观数据抓取")
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])

        if not series_id:
            logger.warning("series_id 未提供，返回空DataFrame")
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])

        url = "https://api.stlouisfed.org/fred/series/observations"
        params: dict[str, str] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if start:
            params["observation_start"] = start.strftime("%Y-%m-%d")
        if end:
            params["observation_end"] = end.strftime("%Y-%m-%d")

        try:
            with get_proxied_client(timeout=30.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"FRED API请求失败 ({series_id}): {e}")
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])

        observations = data.get("observations", [])
        if not observations:
            logger.warning(f"FRED返回空数据 ({series_id})")
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])

        df = pd.DataFrame(observations)
        df = df.rename(columns={"date": "timestamp", "value": "value"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["series_id"] = series_id
        return df[["timestamp", "value", "series_id"]].dropna(subset=["timestamp", "value"])

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新一条宏观数据."""
        end = datetime.now()
        start = end - timedelta(days=7)
        results = []
        for series_id in self.SERIES.values():
            df = self.fetch(start=start, end=end, series_id=series_id)
            if not df.empty:
                results.append(df.tail(1))
        if not results:
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])
        return pd.concat(results, ignore_index=True)

    def fetch_dxy(self) -> pd.DataFrame:
        """抓取美元指数历史数据 — 通过 FRED API."""
        df = self.fetch(series_id="DTWEXBGS")
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "value"])
        return df[["timestamp", "value"]].copy()

    def fetch_yield_curve(self) -> pd.DataFrame:
        """抓取美债收益率曲线 — 2Y vs 10Y."""
        if not self.api_key:
            logger.warning("FRED API key未配置，跳过收益率曲线抓取")
            return pd.DataFrame(columns=["timestamp", "yield_2y", "yield_10y", "spread"])

        end = datetime.now()
        start = end - timedelta(days=365)

        df2 = self.fetch(start=start, end=end, series_id="DGS2")
        df10 = self.fetch(start=start, end=end, series_id="DGS10")

        if df2.empty or df10.empty:
            logger.warning("收益率数据获取失败，返回空DataFrame")
            return pd.DataFrame(columns=["timestamp", "yield_2y", "yield_10y", "spread"])

        merged = pd.merge(
            df2[["timestamp", "value"]].rename(columns={"value": "yield_2y"}),
            df10[["timestamp", "value"]].rename(columns={"value": "yield_10y"}),
            on="timestamp",
            how="outer",
        ).sort_values("timestamp")

        merged["spread"] = merged["yield_10y"] - merged["yield_2y"]
        return merged.dropna(subset=["timestamp"]).reset_index(drop=True)

    def fetch_all_macro(self) -> dict[str, pd.DataFrame]:
        """一次性获取所有宏观指标."""
        return {
            "dxy": self.fetch_dxy(),
            "yield_curve": self.fetch_yield_curve(),
        }

    def fetch_real_rate(self, lookback_days: int = 365) -> pd.DataFrame:
        """获取10年期实际利率 (TIPS)."""
        df = self.fetch(series_id="REAINTRATREARAT10Y")
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "value"])
        return df[["timestamp", "value"]].copy()

    def fetch_breakeven(self, lookback_days: int = 365) -> pd.DataFrame:
        """获取10年期盈亏平衡通胀率 (T10YIE)."""
        df = self.fetch(series_id="T10YIE")
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "value"])
        return df[["timestamp", "value"]].copy()

    def fetch_silver(self) -> pd.DataFrame:
        """获取白银价格 — 上海金交所 Ag99.99 (元/克)."""
        try:
            import akshare as ak
            df = ak.spot_hist_sge(symbol="Ag99.99")
            if not df.empty:
                df = df.rename(columns={"date": "timestamp", "close": "value"})
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df["value"] = pd.to_numeric(df["value"], errors="coerce") / 1000
                return df[["timestamp", "value"]].dropna()
        except Exception as e:
            logger.warning(f"白银数据获取失败: {e}")
            return pd.DataFrame(columns=["timestamp", "value"])
