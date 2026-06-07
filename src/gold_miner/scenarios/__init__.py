"""情景分析模块 — 极端未来事件对黄金价格的影响推演."""

from gold_miner.scenarios.models import (
    HistoricalAnalog,
    ImpactChannel,
    PriceImpactEstimate,
    ScenarioReport,
    StrategyRecommendation,
)
from gold_miner.scenarios.analyzer import ScenarioAnalyzer
from gold_miner.scenarios.store import ScenarioStore

__all__ = [
    "ScenarioAnalyzer",
    "ScenarioReport",
    "ScenarioStore",
    "HistoricalAnalog",
    "ImpactChannel",
    "PriceImpactEstimate",
    "StrategyRecommendation",
]
