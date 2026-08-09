"""信号处理层：技术面、基本面、消息面、情绪面、事件驱动、异常检测、情景分析信号生成."""
from __future__ import annotations

from gold_miner.signals.anomaly import AnomalyDetector, AnomalyReport
from gold_miner.signals.base import DimensionConsensus, Signal, SignalBundle
from gold_miner.signals.event_driven import EventDrivenSignalGenerator, EventSignal
from gold_miner.signals.human_judgment import HumanJudgment, HumanJudgmentStore
from gold_miner.signals.macro_pivot import MacroPivotSignalGenerator
from gold_miner.signals.monitor_signal import MonitorSignalGenerator
from gold_miner.signals.recent_events import RecentEventSignalGenerator
from gold_miner.signals.scenario import ScenarioAnalyzer, ScenarioDefinition
from gold_miner.signals.trust_score import TrustScore, TrustStore

__all__ = [
    "AnomalyDetector",
    "AnomalyReport",
    "DimensionConsensus",
    "EventDrivenSignalGenerator",
    "EventSignal",
    "HumanJudgment",
    "HumanJudgmentStore",
    "MacroPivotSignalGenerator",
    "MonitorSignalGenerator",
    "RecentEventSignalGenerator",
    "ScenarioAnalyzer",
    "ScenarioDefinition",
    "Signal",
    "SignalBundle",
    "TrustScore",
    "TrustStore",
]
