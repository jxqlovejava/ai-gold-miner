"""SPDR Gold Shares (GLD) 持仓数据抓取.

GLD 是全球最大的黄金 ETF 之一，其每日持仓量（吨）是观察机构/散户黄金需求的
重要情绪指标。数据来源为 spdrgoldshares.com 官方历史归档 Excel。
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.proxy import get_proxied_client


class GldHoldingsFetcher(DataFetcher):
    """GLD 每日黄金持仓量获取器."""

    ARCHIVE_URL = (
        "https://api.spdrgoldshares.com/api/v1/historical-archive"
        "?product=gld&exchange=NYSE&lang=en"
    )
    SHEET_NAME = "US GLD Historical Archive"

    def __init__(self, recorder: EconomicDataRecorder | None = None) -> None:
        super().__init__(
            DataSourceMeta(
                name="gld_holdings",
                source="SPDR Gold Shares / World Gold Trust Services",
                frequency="daily",
                description="GLD 每日黄金持仓量（吨）",
                source_tier="T0",
            )
        )
        self._recorder = recorder or EconomicDataRecorder()

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """下载并解析 GLD 历史持仓数据.

        返回 DataFrame 列：timestamp, value（吨）, nav_per_share, shares_volume
        """
        try:
            with get_proxied_client(timeout=60.0) as client:
                resp = client.get(self.ARCHIVE_URL)
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"GLD 持仓数据下载失败: {e}")
            return pd.DataFrame(columns=["timestamp", "value", "nav_per_share", "shares_volume"])

        try:
            df = pd.read_excel(BytesIO(resp.content), sheet_name=self.SHEET_NAME)
        except Exception as e:
            logger.warning(f"GLD Excel 解析失败: {e}")
            return pd.DataFrame(columns=["timestamp", "value", "nav_per_share", "shares_volume"])

        # 标准化列名
        df = df.rename(
            columns={
                "Date": "date",
                "Tonnes of Gold": "value",
                "NAV/Share at 10:30am NYT": "nav_per_share",
                "Daily Share Volume": "shares_volume",
            }
        )

        required = {"date", "value"}
        if not required.issubset(df.columns):
            logger.warning(f"GLD 数据缺少必要列: {required - set(df.columns)}")
            return pd.DataFrame(columns=["timestamp", "value", "nav_per_share", "shares_volume"])

        df["timestamp"] = pd.to_datetime(df["date"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["nav_per_share"] = pd.to_numeric(df.get("nav_per_share"), errors="coerce")
        df["shares_volume"] = pd.to_numeric(df.get("shares_volume"), errors="coerce")

        df = df[["timestamp", "value", "nav_per_share", "shares_volume"]].dropna(
            subset=["timestamp", "value"]
        )
        df = df.sort_values("timestamp").reset_index(drop=True)

        if not df.empty:
            self._persist_latest(df)

        # 应用日期过滤
        if start:
            df = df[df["timestamp"] >= pd.Timestamp(start)]
        if end:
            df = df[df["timestamp"] <= pd.Timestamp(end)]

        return df.reset_index(drop=True)

    def fetch_latest(self) -> pd.DataFrame:
        """获取最新一条 GLD 持仓数据."""
        df = self.fetch()
        if df.empty:
            return df
        return df.tail(1).reset_index(drop=True)

    def _persist_latest(self, df: pd.DataFrame) -> None:
        """将最新一条 GLD 持仓持久化到经济数据库."""
        if df.empty:
            return

        latest = df.iloc[-1]
        previous_value = df.iloc[-2]["value"] if len(df) >= 2 else None
        release_date = latest["timestamp"].strftime("%Y-%m-%d")

        try:
            point = EconomicDataPoint(
                indicator="gld_holdings_tonnes",
                release_date=release_date,
                observation_date=release_date,
                period=release_date[:7],
                actual=float(latest["value"]),
                previous=float(previous_value) if previous_value is not None else None,
                unit="吨",
                source="SPDR Gold Shares / World Gold Trust Services",
                source_tier="T0",
                impact="medium",
                notes=f"GLD 每日黄金持仓量，NAV/Share {latest.get('nav_per_share')}，成交量 {latest.get('shares_volume')}",
            )
            self._recorder.save(point)
        except Exception as e:
            logger.warning(f"持久化 GLD 持仓数据失败: {e}")
