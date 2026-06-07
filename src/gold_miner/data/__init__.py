"""数据采集层：负责所有数据源的统一抓取."""

from gold_miner.data.accumulation_gold import AccumulationGoldFetcher
from gold_miner.data.central_bank import CentralBankData, CentralBankFetcher
from gold_miner.data.macro import MacroDataFetcher
from gold_miner.data.polymarket import PolymarketFetcher
from gold_miner.data.spot_gold import SpotGoldFetcher

__all__ = [
    "SpotGoldFetcher",
    "AccumulationGoldFetcher",
    "CentralBankData",
    "CentralBankFetcher",
    "MacroDataFetcher",
    "PolymarketFetcher",
]
