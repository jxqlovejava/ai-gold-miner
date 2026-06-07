"""情绪面数据 — 国内上期所 AU 期货持仓 + 金银比 (不再依赖 Yahoo Finance)."""

from datetime import datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta


class SentimentDataFetcher(DataFetcher):
    """市场情绪数据获取器 — 国内数据源."""

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="sentiment",
                source="上期所 AU 期货 (AKShare) / 金银比",
                frequency="daily",
                description="AU 期货持仓量、成交量、量价关系",
            )
        )

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        return self.fetch_au_futures(start, end)

    def fetch_latest(self) -> pd.DataFrame:
        return self.fetch_au_futures()

    def fetch_au_futures(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        lookback: int = 60,
    ) -> pd.DataFrame:
        """获取上期所 AU 期货主力连续合约数据 (含持仓量).

        返回标准化 DataFrame:
        - timestamp, open, high, low, close, volume, open_interest
        """
        try:
            df = ak.futures_main_sina(symbol="AU0")
            if df.empty:
                return pd.DataFrame(columns=[
                    "timestamp", "open", "high", "low", "close", "volume", "open_interest",
                ])

            df = df.rename(columns={
                "日期": "timestamp",
                "开盘价": "open",
                "最高价": "high",
                "最低价": "low",
                "收盘价": "close",
                "成交量": "volume",
                "持仓量": "open_interest",
            })
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            for col in ["open", "high", "low", "close", "volume", "open_interest"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            # 计算衍生指标
            df["oi_change"] = df["open_interest"].diff()
            df["oi_change_5d"] = df["open_interest"].diff(5)
            df["volume_ma5"] = df["volume"].rolling(5).mean()
            df["volume_ratio"] = df["volume"] / df["volume_ma5"].clip(lower=1)

            # 基差: 期货收盘 - 开盘 ≈ 日内方向 (简化版)
            df["intraday_bias"] = df["close"] - df["open"]

            if start:
                df = df[df["timestamp"] >= pd.Timestamp(start)]
            if end:
                df = df[df["timestamp"] <= pd.Timestamp(end)]
            if lookback and not start:
                df = df.tail(lookback)

            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as e:
            logger.warning(f"AU期货数据获取失败: {e}")
            return pd.DataFrame(columns=[
                "timestamp", "open", "high", "low", "close", "volume", "open_interest",
            ])
