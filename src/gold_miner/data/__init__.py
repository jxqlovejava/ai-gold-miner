"""数据采集层：负责所有数据源的统一抓取.

可选依赖: akshare, yfinance — 缺失时对应模块不可用，但不影响 sentinel 等核心功能.
"""

from __future__ import annotations

try:
    from gold_miner.data.accumulation_gold import AccumulationGoldFetcher
except ImportError:
    AccumulationGoldFetcher = None  # type: ignore[assignment]

try:
    from gold_miner.data.central_bank import (
        CentralBankData,
        CentralBankFetcher,
        CentralBankHistoryFetcher,
    )
except ImportError:
    CentralBankData = None  # type: ignore[assignment]
    CentralBankFetcher = None  # type: ignore[assignment]
    CentralBankHistoryFetcher = None  # type: ignore[assignment]

try:
    from gold_miner.data.fiscal import FiscalDataFetcher, FiscalSnapshot
except ImportError:
    FiscalDataFetcher = None  # type: ignore[assignment]
    FiscalSnapshot = None  # type: ignore[assignment]

try:
    from gold_miner.data.long_term_aggregator import LongTermDataAggregator, LongTermDataBundle
except ImportError:
    LongTermDataAggregator = None  # type: ignore[assignment]
    LongTermDataBundle = None  # type: ignore[assignment]

try:
    from gold_miner.data.macro import MacroDataFetcher
except ImportError:
    MacroDataFetcher = None  # type: ignore[assignment]

try:
    from gold_miner.data.polymarket import PolymarketFetcher
except ImportError:
    PolymarketFetcher = None  # type: ignore[assignment]

try:
    from gold_miner.data.spot_gold import SpotGoldFetcher
except ImportError:
    SpotGoldFetcher = None  # type: ignore[assignment]

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
