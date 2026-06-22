"""数据采集层：负责所有数据源的统一抓取."""

from __future__ import annotations

from gold_miner.data.accumulation_gold import AccumulationGoldFetcher
from gold_miner.data.central_bank import (
    CentralBankData,
    CentralBankFetcher,
    CentralBankHistoryFetcher,
)
from gold_miner.data.fiscal import FiscalDataFetcher, FiscalSnapshot
from gold_miner.data.long_term_aggregator import LongTermDataAggregator, LongTermDataBundle
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.polymarket import PolymarketFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher

__all__ = [
    "AccumulationGoldFetcher",
    "CentralBankData",
    "CentralBankFetcher",
    "CentralBankHistoryFetcher",
    "FiscalDataFetcher",
    "FiscalSnapshot",
    "LongTermDataAggregator",
    "LongTermDataBundle",
    "MacroDataFetcher",
    "PolymarketFetcher",
    "SpotGoldFetcher",
]
