"""宏观数据抓取 — 美元指数、利率、通胀."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.config import settings
from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.proxy import get_proxied_client


class MacroDataFetcher(DataFetcher):
    """宏观数据获取器 — FRED + Yahoo Finance."""

    SERIES = {
        # FRED DTWEXBGS 是贸易加权美元指数(广义)，水平约 120，不是 ICE DXY(~100)
        "trade_weighted_usd": "DTWEXBGS",
        "real_rate_10y": "REAINTRATREARAT10Y",
        "breakeven_10y": "T10YIE",
        "fed_rate": "DFF",
        "cpi_index": "CPIAUCSL",
        "ppi_index": "PPIACO",
        "unemployment_rate": "UNRATE",
    }

    SERIES_META: dict[str, dict[str, str]] = {
        "DTWEXBGS": {
            "indicator": "trade_weighted_usd",
            "name": "贸易加权美元指数(广义)",
            "unit": "index",
            "impact": "high",
            "frequency": "daily",
        },
        "REAINTRATREARAT10Y": {
            "indicator": "real_rate_10y",
            "name": "美国10年期TIPS实际利率",
            "unit": "%",
            "impact": "high",
            "frequency": "daily",
        },
        "T10YIE": {
            "indicator": "breakeven_10y",
            "name": "美国10年期盈亏平衡通胀率",
            "unit": "%",
            "impact": "medium",
            "frequency": "daily",
        },
        "DFF": {
            "indicator": "fed_rate",
            "name": "美国联邦基金利率",
            "unit": "%",
            "impact": "high",
            "frequency": "daily",
        },
        "CPIAUCSL": {
            "indicator": "cpi_index",
            "name": "美国CPI指数",
            "unit": "index",
            "impact": "high",
            "frequency": "monthly",
        },
        "PPIACO": {
            "indicator": "ppi_index",
            "name": "美国PPI指数",
            "unit": "index",
            "impact": "high",
            "frequency": "monthly",
        },
        "UNRATE": {
            "indicator": "unemployment_rate",
            "name": "美国失业率",
            "unit": "%",
            "impact": "high",
            "frequency": "monthly",
        },
    }

    def __init__(self, recorder: EconomicDataRecorder | None = None) -> None:
        super().__init__(
            DataSourceMeta(
                name="macro",
                source="FRED / Yahoo Finance",
                frequency="day",
                description="美元指数、利率、通胀等宏观指标",
                source_tier="T0",  # FRED 为美联储官方一手数据
            )
        )
        self.api_key = settings.fred_api_key
        self._recorder = recorder or EconomicDataRecorder()

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        series_id: str | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """从FRED获取宏观数据.

        Args:
            series_id: FRED series ID，如 'DTWEXBGS' (贸易加权美元指数)
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
        df = df[["timestamp", "value", "series_id"]].dropna(subset=["timestamp", "value"])

        # 自动持久化到经济数据库（仅限已知宏观经济指标）
        if not df.empty and series_id in self.SERIES_META:
            self._persist_series(df, series_id)

        return df

    def fetch_latest(self) -> pd.DataFrame:
        """抓取最新一条宏观数据.

        实际抓取由 fetch() 完成，fetch() 会自动持久化已知宏观经济指标。
        """
        end = datetime.now()
        results: list[pd.DataFrame] = []
        for series_id in self.SERIES.values():
            meta = self.SERIES_META.get(series_id, {})
            frequency = meta.get("frequency", "daily")
            lookback_days = 90 if frequency == "monthly" else 7
            start = end - timedelta(days=lookback_days)

            df = self.fetch(start=start, end=end, series_id=series_id)
            if not df.empty:
                results.append(df.tail(1))
        if not results:
            return pd.DataFrame(columns=["timestamp", "value", "series_id"])
        return pd.concat(results, ignore_index=True)

    def _persist_series(self, df: pd.DataFrame, series_id: str) -> None:
        """将 FRED 时间序列的最新值持久化为 EconomicDataPoint."""
        if df.empty or len(df) < 1:
            return

        meta = self.SERIES_META.get(series_id)
        if not meta:
            return

        latest = df.iloc[-1]
        previous_value = df.iloc[-2]["value"] if len(df) >= 2 else None
        observation_date = pd.Timestamp(latest["timestamp"]).strftime("%Y-%m-%d")
        release_date = datetime.now().strftime("%Y-%m-%d")
        frequency = meta.get("frequency", "daily")
        period = observation_date[:7] if frequency == "monthly" else observation_date

        try:
            point = EconomicDataPoint(
                indicator=meta["indicator"],
                release_date=release_date,
                observation_date=observation_date,
                period=period,
                actual=float(latest["value"]),
                previous=float(previous_value) if previous_value is not None else None,
                unit=meta["unit"],
                source="FRED / Federal Reserve Economic Data",
                source_tier="T0",
                impact=meta.get("impact", "high"),
                notes=f"自动抓取自 FRED series {series_id}，观测日期 {observation_date}",
            )
            self._recorder.save(point)
        except Exception as e:
            logger.warning(f"持久化宏观数据失败 ({series_id}): {e}")

    def fetch_dxy(self) -> pd.DataFrame:
        """抓取 ICE 美元指数 (DXY) 历史数据 — Yahoo Finance ``DX-Y.NYB``.

        注意: 不要与 FRED ``DTWEXBGS``（贸易加权美元指数，水平约 120）混淆。
        交易者口中的 DXY 指 ICE Dollar Index，水平约 100。
        """
        from time import sleep as _sleep

        symbol = settings.yahoo_symbol_dxy
        for attempt in range(3):
            try:
                import yfinance as yf

                if attempt > 0:
                    _sleep(2 ** attempt)  # exponential backoff

                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1y")
                if hist is None or hist.empty:
                    logger.warning(f"Yahoo Finance 返回空 DXY 数据 ({symbol})")
                    return pd.DataFrame(columns=["timestamp", "value"])

                df = hist.reset_index()
                # yfinance 索引列可能是 Date 或 Datetime
                date_col = "Date" if "Date" in df.columns else "Datetime"
                if date_col not in df.columns:
                    # 已是普通列名场景
                    date_col = df.columns[0]
                df = df.rename(columns={date_col: "timestamp", "Close": "value"})
                df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                out = df[["timestamp", "value"]].dropna().reset_index(drop=True)
                if out.empty:
                    logger.warning(f"Yahoo Finance DXY 解析后为空 ({symbol})")
                return out
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"ICE DXY 获取失败 (attempt {attempt + 1}/3, {symbol}): {e}")
                else:
                    logger.warning(f"ICE DXY 获取失败 ({symbol}): {e}")
        return pd.DataFrame(columns=["timestamp", "value"])

    def fetch_trade_weighted_usd(self) -> pd.DataFrame:
        """抓取贸易加权美元指数(广义) — FRED DTWEXBGS.

        水平约 120，不是 ICE DXY。用于宏观研究时需明确标注指标名称。
        """
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
            "dxy": self.fetch_dxy(),  # ICE DXY (~100)
            "trade_weighted_usd": self.fetch_trade_weighted_usd(),  # FRED DTWEXBGS (~120)
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

    def fetch_vix(self) -> pd.DataFrame:
        """获取 CBOE 波动率指数 VIX — FRED series VIXCLS."""
        try:
            return self.fetch("VIXCLS", "VIX")
        except Exception:
            return pd.DataFrame(columns=["timestamp", "value"])

    def fetch_fear_greed(self, days: int = 7) -> pd.DataFrame:
        """获取恐惧贪婪指数 — alternative.me Crypto Fear & Greed Index (可作为市场风险偏好参考).

        返回含 timestamp + value 的标准 DataFrame。
        """
        try:
            import json
            import urllib.request
            url = f"https://api.alternative.me/fng/?limit={min(days, 365)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            records: list[dict[str, Any]] = []
            for item in data.get("data", []):
                records.append({
                    "timestamp": pd.Timestamp(item["timestamp"], unit="s"),
                    "value": float(item["value"]),
                })
            if records:
                return pd.DataFrame(records).sort_values("timestamp")
            return pd.DataFrame(columns=["timestamp", "value"])
        except Exception:
            return pd.DataFrame(columns=["timestamp", "value"])

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


# 便捷别名
MacroFetcher = MacroDataFetcher


def fetch_macro_data(lookback_days: int = 365) -> dict[str, pd.DataFrame]:
    """便捷函数：一次性获取所有宏观指标.

    Args:
        lookback_days: 回溯天数

    Returns:
        dict with keys: dxy (ICE), trade_weighted_usd (FRED DTWEXBGS),
                        yield_curve, real_rate, breakeven, silver
    """
    fetcher = MacroDataFetcher()
    result = fetcher.fetch_all_macro()
    result["real_rate"] = fetcher.fetch_real_rate(lookback_days)
    result["breakeven"] = fetcher.fetch_breakeven(lookback_days)
    result["silver"] = fetcher.fetch_silver()
    return result
