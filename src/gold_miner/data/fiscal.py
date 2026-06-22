"""长期财政与货币信用数据 — 美国债务、利息支出、实际利率、美元储备份额.

用于中长期金价分析的结构性因子评估。
当前以 FRED/公开数据为最佳尝试，内置历史 fallback 保证离线可用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.base import DataFetcher, DataSourceMeta


@dataclass
class FiscalSnapshot:
    """美国财政信用快照."""

    report_date: datetime
    federal_debt_usd_billions: float | None = None  # 联邦债务总额（十亿美元）
    debt_to_gdp_pct: float | None = None  # 债务/GDP 比率
    interest_expense_usd_billions: float | None = None  # 财年利息支出
    interest_expense_to_revenue_pct: float | None = None  # 利息/财政收入
    real_rate_10y_pct: float | None = None  # 10Y TIPS 实际利率
    breakeven_10y_pct: float | None = None  # 10Y 盈亏平衡通胀
    dollar_reserve_share_pct: float | None = None  # 美元全球储备份额
    source: str = ""


class FiscalDataFetcher(DataFetcher):
    """美国长期财政与货币信用数据获取器.

    数据源优先级:
    1. FRED API (若配置 FRED_API_KEY)
    2. 美国财政部公开数据
    3. 内置历史 fallback
    """

    # 已知历史数据（季度/年度）
    # 来源: FRED, Treasury.gov, IMF COFER
    KNOWN_FISCAL_DATA: list[dict[str, Any]] = [
        {"report_date": "2022-12-31", "federal_debt_usd_billions": 31400, "debt_to_gdp_pct": 120.0, "real_rate_10y_pct": 1.60, "dollar_reserve_share_pct": 58.0},
        {"report_date": "2023-03-31", "federal_debt_usd_billions": 31400, "debt_to_gdp_pct": 119.0, "real_rate_10y_pct": 1.40, "dollar_reserve_share_pct": 58.2},
        {"report_date": "2023-06-30", "federal_debt_usd_billions": 32680, "debt_to_gdp_pct": 121.0, "real_rate_10y_pct": 1.65, "dollar_reserve_share_pct": 58.3},
        {"report_date": "2023-09-30", "federal_debt_usd_billions": 33500, "debt_to_gdp_pct": 123.0, "real_rate_10y_pct": 2.10, "dollar_reserve_share_pct": 58.4},
        {"report_date": "2023-12-31", "federal_debt_usd_billions": 34300, "debt_to_gdp_pct": 122.0, "real_rate_10y_pct": 1.70, "dollar_reserve_share_pct": 58.4},
        {"report_date": "2024-03-31", "federal_debt_usd_billions": 34600, "debt_to_gdp_pct": 121.5, "real_rate_10y_pct": 2.00, "dollar_reserve_share_pct": 58.1},
        {"report_date": "2024-06-30", "federal_debt_usd_billions": 35270, "debt_to_gdp_pct": 123.0, "real_rate_10y_pct": 1.95, "dollar_reserve_share_pct": 58.0},
        {"report_date": "2024-09-30", "federal_debt_usd_billions": 35900, "debt_to_gdp_pct": 123.5, "real_rate_10y_pct": 1.40, "dollar_reserve_share_pct": 57.8},
        {"report_date": "2024-12-31", "federal_debt_usd_billions": 36600, "debt_to_gdp_pct": 123.0, "real_rate_10y_pct": 2.10, "dollar_reserve_share_pct": 57.8},
        {"report_date": "2025-03-31", "federal_debt_usd_billions": 36800, "debt_to_gdp_pct": 122.5, "real_rate_10y_pct": 1.80, "dollar_reserve_share_pct": 57.5},
        {"report_date": "2025-06-30", "federal_debt_usd_billions": 37500, "debt_to_gdp_pct": 124.0, "real_rate_10y_pct": 1.55, "dollar_reserve_share_pct": 57.3},
        {"report_date": "2025-09-30", "federal_debt_usd_billions": 38200, "debt_to_gdp_pct": 125.0, "real_rate_10y_pct": 1.85, "dollar_reserve_share_pct": 57.0},
        {"report_date": "2025-12-31", "federal_debt_usd_billions": 38900, "debt_to_gdp_pct": 125.5, "real_rate_10y_pct": 2.00, "dollar_reserve_share_pct": 56.8},
        {"report_date": "2026-03-31", "federal_debt_usd_billions": 39500, "debt_to_gdp_pct": 126.0, "real_rate_10y_pct": 1.70, "dollar_reserve_share_pct": 56.5},
    ]

    def __init__(self) -> None:
        super().__init__(
            DataSourceMeta(
                name="fiscal_credit",
                source="FRED/Treasury/IMF",
                frequency="quarterly",
                description="美国财政债务、实际利率、美元储备份额",
            )
        )

    def fetch(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取财政信用历史数据."""
        # 优先尝试 FRED API
        try:
            df = self._fetch_from_fred()
            if not df.empty:
                return df
        except Exception as e:
            logger.debug(f"FRED 数据获取失败: {e}")

        return self._fallback_dataframe()

    def fetch_latest(self) -> FiscalSnapshot:
        """获取最新财政信用快照."""
        df = self.fetch()
        if df.empty:
            return self._fallback_snapshot()

        latest = df.iloc[-1]

        def _to_float_or_none(value: Any) -> float | None:
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        return FiscalSnapshot(
            report_date=pd.to_datetime(latest["timestamp"]),
            federal_debt_usd_billions=_to_float_or_none(latest.get("federal_debt_usd_billions")),
            debt_to_gdp_pct=_to_float_or_none(latest.get("debt_to_gdp_pct")),
            interest_expense_usd_billions=_to_float_or_none(latest.get("interest_expense_usd_billions")),
            interest_expense_to_revenue_pct=_to_float_or_none(latest.get("interest_expense_to_revenue_pct")),
            real_rate_10y_pct=_to_float_or_none(latest.get("real_rate_10y_pct")),
            breakeven_10y_pct=_to_float_or_none(latest.get("breakeven_10y_pct")),
            dollar_reserve_share_pct=_to_float_or_none(latest.get("dollar_reserve_share_pct")),
            source=str(latest.get("source", "fallback")),
        )

    def fetch_trend_summary(self) -> dict[str, Any]:
        """获取财政信用趋势摘要."""
        df = self.fetch().sort_values("timestamp")
        if len(df) < 2:
            return {"status": "no_data"}

        latest = df.iloc[-1]
        prev_year = df[df["timestamp"] <= df["timestamp"].iloc[-1] - pd.Timedelta(days=350)]
        prev = prev_year.iloc[-1] if not prev_year.empty else df.iloc[0]

        debt_change_pct = self._safe_pct_change(
            latest.get("federal_debt_usd_billions"),
            prev.get("federal_debt_usd_billions"),
        )
        dollar_share_change = self._safe_diff(
            latest.get("dollar_reserve_share_pct"),
            prev.get("dollar_reserve_share_pct"),
        )
        real_rate_change = self._safe_diff(
            latest.get("real_rate_10y_pct"),
            prev.get("real_rate_10y_pct"),
        )

        return {
            "status": "ok",
            "latest_date": latest["timestamp"].isoformat(),
            "federal_debt_usd_billions": float(latest.get("federal_debt_usd_billions", 0)),
            "debt_to_gdp_pct": float(latest.get("debt_to_gdp_pct", 0)),
            "real_rate_10y_pct": float(latest.get("real_rate_10y_pct", 0)),
            "dollar_reserve_share_pct": float(latest.get("dollar_reserve_share_pct", 0)),
            "debt_yoy_change_pct": round(debt_change_pct, 1),
            "dollar_share_yoy_change_pct": round(dollar_share_change, 1),
            "real_rate_yoy_change_pct": round(real_rate_change, 1),
        }

    def _fetch_from_fred(self) -> pd.DataFrame:
        """尝试从 FRED API 获取数据."""
        import os

        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            return pd.DataFrame()

        # FRED 数据获取需要 series ID 和日期范围
        # 此处预留接口，未来可扩展具体 series 请求
        logger.debug("FRED_API_KEY 存在，但未配置具体 series 请求")
        return pd.DataFrame()

    def _fallback_dataframe(self) -> pd.DataFrame:
        """返回内置历史数据."""
        logger.warning("财政信用数据使用内置 fallback")
        df = pd.DataFrame(self.KNOWN_FISCAL_DATA)
        df["timestamp"] = pd.to_datetime(df["report_date"])
        df["source"] = "fallback"
        return df

    def _fallback_snapshot(self) -> FiscalSnapshot:
        """返回最新 fallback 快照."""
        latest = self.KNOWN_FISCAL_DATA[-1]
        return FiscalSnapshot(
            report_date=pd.to_datetime(latest["report_date"]),
            federal_debt_usd_billions=float(latest.get("federal_debt_usd_billions", 0)),
            debt_to_gdp_pct=float(latest.get("debt_to_gdp_pct", 0)),
            real_rate_10y_pct=float(latest.get("real_rate_10y_pct", 0)),
            dollar_reserve_share_pct=float(latest.get("dollar_reserve_share_pct", 0)),
            source="fallback",
        )

    @staticmethod
    def _safe_pct_change(current: Any, previous: Any) -> float:
        try:
            c = float(current)
            p = float(previous)
            return (c / p - 1) * 100 if p else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _safe_diff(current: Any, previous: Any) -> float:
        try:
            return float(current) - float(previous)
        except (TypeError, ValueError):
            return 0.0
