"""CFTC Commitments of Traders (COT) 数据抓取.

从 CFTC Socrata 开放数据 API 抓取黄金期货持仓报告，按参与者类别拆分：
- Managed Money (对冲基金/投机者)
- Producer/Merchant (商业套保者)
- Swap Dealers (掉期交易商)
- Non-Reportable (散户)

数据每周更新一次，报告日期为周二，周五发布。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta
from gold_miner.data.economic_data import EconomicDataPoint, EconomicDataRecorder
from gold_miner.proxy import get_proxied_client


class CotDataFetcher(DataFetcher):
    """CFTC 黄金期货持仓报告获取器."""

    API_URL = "https://publicreporting.cftc.gov/resource/72hh-3qpy.csv"

    # 黄金合约代码 (COMEX)
    GOLD_CODES = {"088691": "GOLD_stock", "088695": "MICRO_GOLD"}

    # 我们关心的持仓列
    KEY_COLUMNS = {
        "report_date_as_yyyy_mm_dd": "report_date",
        "cftc_contract_market_code": "contract_code",
        "commodity_name": "commodity",
        "open_interest_all": "open_interest",
        "prod_merc_positions_long": "producer_long",
        "prod_merc_positions_short": "producer_short",
        "swap_positions_long_all": "swap_long",
        "swap__positions_short_all": "swap_short",
        "swap__positions_spread_all": "swap_spread",
        "m_money_positions_long_all": "managed_money_long",
        "m_money_positions_short_all": "managed_money_short",
        "m_money_positions_spread": "managed_money_spread",
        "nonrept_positions_long_all": "non_reportable_long",
        "nonrept_positions_short_all": "non_reportable_short",
    }

    METRICS = [
        "open_interest",
        "producer_long", "producer_short",
        "swap_long", "swap_short", "swap_spread",
        "managed_money_long", "managed_money_short", "managed_money_spread",
        "non_reportable_long", "non_reportable_short",
    ]

    def __init__(self, recorder: EconomicDataRecorder | None = None) -> None:
        super().__init__(
            DataSourceMeta(
                name="cftc_cot",
                source="CFTC (Commodity Futures Trading Commission)",
                frequency="weekly",
                description="黄金期货持仓报告 — 按参与者类别拆分",
                source_tier="T0",
            )
        )
        self._recorder = recorder or EconomicDataRecorder()

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        contract: str = "088691",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """从 CFTC API 获取黄金 COT 数据.

        Args:
            contract: 合约代码，"088691"=标准合约，"088695"=微型合约
        """
        where_parts = [f"cftc_contract_market_code='{contract}'"]
        if start:
            where_parts.append(f"report_date_as_yyyy_mm_dd>='{start.strftime('%Y-%m-%d')}'")
        if end:
            where_parts.append(f"report_date_as_yyyy_mm_dd<='{end.strftime('%Y-%m-%d')}'")

        where_clause = " AND ".join(where_parts)
        params = {
            "$where": where_clause,
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": "2000",
        }

        try:
            with get_proxied_client(timeout=30.0) as client:
                resp = client.get(self.API_URL, params=params)
                resp.raise_for_status()
        except Exception as e:
            logger.warning(f"CFTC COT 数据下载失败: {e}")
            return pd.DataFrame(columns=["timestamp"] + self.METRICS)

        try:
            df = pd.read_csv(pd.io.common.StringIO(resp.text))
        except Exception as e:
            logger.warning(f"CFTC CSV 解析失败: {e}")
            return pd.DataFrame(columns=["timestamp"] + self.METRICS)

        if df.empty:
            return pd.DataFrame(columns=["timestamp"] + self.METRICS)

        df = df.rename(columns=self.KEY_COLUMNS)
        keep_cols = ["report_date"] + [c for c in self.METRICS if c in df.columns]
        df = df[[c for c in keep_cols if c in df.columns]]

        df["timestamp"] = pd.to_datetime(df["report_date"], errors="coerce")
        for col in self.METRICS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        df = df.drop(columns=["report_date"], errors="ignore")
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        if not df.empty:
            self._persist_latest(df, contract)

        return df

    def fetch_latest(self) -> pd.DataFrame:
        end = datetime.now()
        start = end - timedelta(days=90)
        df = self.fetch(start=start, end=end)
        if df.empty:
            return df
        return df.tail(1).reset_index(drop=True)

    def _persist_latest(self, df: pd.DataFrame, contract: str) -> None:
        """将最新 COT 报告持久化到经济数据库."""
        if df.empty:
            return

        contract_label = self.GOLD_CODES.get(contract, contract)
        latest = df.iloc[-1]
        previous_row = df.iloc[-2] if len(df) >= 2 else None
        release_date = latest["timestamp"].strftime("%Y-%m-%d")

        for metric in ["managed_money_long", "managed_money_short", "open_interest"]:
            if metric not in latest:
                continue
            try:
                previous_value = (
                    float(previous_row[metric]) if previous_row is not None and metric in previous_row else None
                )
                point = EconomicDataPoint(
                    indicator=f"cot_{contract_label}_{metric}",
                    release_date=release_date,
                    observation_date=release_date,
                    period=release_date[:7],
                    actual=float(latest[metric]),
                    previous=previous_value,
                    unit="合约手",
                    source="CFTC / COT Disaggregated Report",
                    source_tier="T0",
                    impact=self._metric_impact(metric),
                    notes=f"黄金 COT {contract_label} {metric}",
                )
                self._recorder.save(point)
            except Exception as e:
                logger.warning(f"持久化 COT 数据失败 ({metric}): {e}")

    @staticmethod
    def _metric_impact(metric: str) -> str:
        if "managed_money" in metric:
            return "high"  # 投机者头寸变化对金价影响最大
        if "open_interest" in metric:
            return "medium"
        return "low"
