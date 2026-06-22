"""中长期数据聚合器 — 统一获取长期分析所需的全部数据.

聚合维度:
- 央行购金历史趋势
- 国际黄金 ETF 持仓与资金流
- CFTC COT 持仓
- 美国财政/实际利率/美元储备份额
- 现货黄金长期价格序列
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from gold_miner.data.central_bank import CentralBankHistoryFetcher
from gold_miner.data.cot_report import CotReportFetcher
from gold_miner.data.etf_flow import IntlGoldEtfFlowFetcher
from gold_miner.data.fiscal import FiscalDataFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher


@dataclass
class LongTermDataBundle:
    """中长期分析数据包."""

    current_spot: float = 0.0
    central_bank_trend: dict[str, Any] = field(default_factory=dict)
    central_bank_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    intl_etf_summary: dict[str, Any] = field(default_factory=dict)
    intl_etf_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    cot_summary: dict[str, Any] = field(default_factory=dict)
    cot_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    fiscal_summary: dict[str, Any] = field(default_factory=dict)
    fiscal_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    gold_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    fetched_at: datetime = field(default_factory=datetime.now)


class LongTermDataAggregator:
    """中长期数据聚合器.

    负责并发/串行获取所有长期分析所需数据，并打包成 LongTermDataBundle。
    每个子获取器独立失败不影响其他数据。
    """

    def __init__(self) -> None:
        self.cb_fetcher = CentralBankHistoryFetcher()
        self.etf_fetcher = IntlGoldEtfFlowFetcher()
        self.cot_fetcher = CotReportFetcher()
        self.fiscal_fetcher = FiscalDataFetcher()
        self.spot_fetcher = SpotGoldFetcher()

    def fetch_all(self, gold_lookback_days: int = 365) -> LongTermDataBundle:
        """获取全部中长期数据."""
        bundle = LongTermDataBundle()

        # 1. 现货黄金价格历史与当前价 (优先国际金价 USD/oz)
        try:
            bundle.gold_history = self.spot_fetcher.fetch(days=gold_lookback_days)
            if not bundle.gold_history.empty:
                bundle.current_spot = float(bundle.gold_history["close"].iloc[-1])
        except Exception as e:
            logger.warning(f"现货黄金历史获取失败: {e}")

        try:
            intl_quote = self.spot_fetcher.fetch_international_quote()
            # fetch_international_quote 可能返回 list[dict] 或 dict
            if isinstance(intl_quote, list) and intl_quote:
                for item in intl_quote:
                    if item and "伦敦金" in item.get("name", "") and item.get("price"):
                        bundle.current_spot = float(item["price"])
                        break
            elif isinstance(intl_quote, dict) and intl_quote.get("price"):
                bundle.current_spot = float(intl_quote["price"])
        except Exception as e:
            logger.debug(f"国际金价实时报价获取失败: {e}")

        if bundle.current_spot == 0.0:
            bundle.current_spot = 3300.0

        # 2. 央行购金历史趋势
        try:
            bundle.central_bank_trend = self.cb_fetcher.fetch_rolling_trend(quarters=8)
            bundle.central_bank_history = self.cb_fetcher.fetch_quarterly_history()
        except Exception as e:
            logger.warning(f"央行购金趋势获取失败: {e}")
            bundle.central_bank_trend = {"status": "error"}

        # 3. 国际 ETF 资金流
        try:
            bundle.intl_etf_summary = self.etf_fetcher.fetch_flow_summary()
            bundle.intl_etf_history = self.etf_fetcher.fetch()
        except Exception as e:
            logger.warning(f"国际ETF数据获取失败: {e}")
            bundle.intl_etf_summary = {"status": "error"}

        # 4. COT 持仓
        try:
            bundle.cot_summary = self.cot_fetcher.fetch_net_position(weeks=12)
            bundle.cot_history = self.cot_fetcher.fetch()
        except Exception as e:
            logger.warning(f"COT数据获取失败: {e}")
            bundle.cot_summary = {"status": "error"}

        # 5. 财政信用数据
        try:
            bundle.fiscal_summary = self.fiscal_fetcher.fetch_trend_summary()
            bundle.fiscal_history = self.fiscal_fetcher.fetch()
        except Exception as e:
            logger.warning(f"财政信用数据获取失败: {e}")
            bundle.fiscal_summary = {"status": "error"}

        bundle.fetched_at = datetime.now()
        return bundle
