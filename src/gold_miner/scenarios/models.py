"""情景分析数据模型."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class HistoricalAnalog:
    """历史类比事件."""

    event_name: str
    period: str  # e.g. "2008-2009"
    gold_price_change_pct: float  # 期间金价涨跌幅
    similarity_score: float  # 0.0-1.0 与当前情景的相似度
    key_parallels: list[str] = field(default_factory=list)
    key_differences: list[str] = field(default_factory=list)


@dataclass
class ImpactChannel:
    """传导路径 — 事件如何影响金价."""

    channel: str  # 利率/美元/避险/通胀/央行购金/...
    direction: str  # bullish / bearish
    magnitude: str  # strong / moderate / weak
    description: str = ""
    timeframe: str = ""  # immediate / short-term / medium-term / long-term


@dataclass
class PriceImpactEstimate:
    """价格影响量化估计."""

    direction: str  # bullish / bearish / neutral
    base_case_change_pct: float = 0.0
    bullish_case_change_pct: float = 0.0
    bearish_case_change_pct: float = 0.0
    peak_impact_months: int = 0  # 影响峰值时间（月）
    confidence: float = 0.0  # 0.0-1.0
    reasoning: str = ""


@dataclass
class StrategyRecommendation:
    """应对策略建议."""

    overall_position: str  # 增持/减持/观望/对冲
    spot_gold_action: str = ""
    accumulation_gold_action: str = ""
    suggested_entry_zones: list[float] = field(default_factory=list)
    suggested_exit_zones: list[float] = field(default_factory=list)
    hedging_suggestions: list[str] = field(default_factory=list)
    position_sizing: str = ""  # e.g. "不超过总资产30%"
    rebalancing_frequency: str = ""  # e.g. "每季度审视"


@dataclass
class ScenarioReport:
    """情景分析完整报告."""

    id: str
    created_at: datetime = field(default_factory=datetime.now)

    # 输入
    scenario_description: str = ""
    time_horizon_months: int = 12

    # 上下文
    context_snapshot: dict[str, Any] = field(default_factory=dict)

    # 分析
    trigger_conditions: list[str] = field(default_factory=list)
    transmission_channels: list[ImpactChannel] = field(default_factory=list)
    historical_analogs: list[HistoricalAnalog] = field(default_factory=list)
    price_impact: PriceImpactEstimate | None = None
    key_levels: list[float] = field(default_factory=list)
    probability_assessment: str = ""

    # 策略
    strategy: StrategyRecommendation | None = None

    # 监控
    risk_factors: list[str] = field(default_factory=list)
    monitoring_indicators: list[str] = field(default_factory=list)

    # 可选：关联预测追踪
    prediction_id: str | None = None

    @property
    def summary(self) -> str:
        if self.price_impact is None:
            return f"情景分析: {self.scenario_description[:80]}..."
        pi = self.price_impact
        direction_cn = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性"}
        return (
            f"[{direction_cn.get(pi.direction, pi.direction)}] "
            f"基准±{pi.base_case_change_pct:+.1f}% "
            f"| 峰值{pi.peak_impact_months}个月 "
            f"| 置信度{pi.confidence:.0%} "
            f"| {self.scenario_description[:60]}..."
        )
