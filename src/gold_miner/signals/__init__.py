"""信号处理层：技术面、基本面、消息面、情绪面、事件驱动、异常检测、情景分析信号生成."""

from gold_miner.signals.anomaly import AnomalyDetector, AnomalyReport
from gold_miner.signals.event_driven import EventDrivenSignalGenerator, EventSignal
from gold_miner.signals.human_judgment import HumanJudgment, HumanJudgmentStore
from gold_miner.signals.pipeline import PipelineContext, PipelineStep, SignalPipeline
from gold_miner.signals.scenario import ScenarioAnalyzer, ScenarioDefinition
from gold_miner.signals.trust_score import TrustScore, TrustStore

__all__ = [
    "AnomalyDetector",
    "AnomalyReport",
    "EventDrivenSignalGenerator",
    "EventSignal",
    "HumanJudgment",
    "HumanJudgmentStore",
    "PipelineContext",
    "PipelineStep",
    "ScenarioAnalyzer",
    "ScenarioDefinition",
    "SignalPipeline",
    "TrustScore",
    "TrustStore",
]
