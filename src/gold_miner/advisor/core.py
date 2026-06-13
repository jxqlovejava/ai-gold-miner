"""advisor 核心 — 协议、数据模型、基类.

设计原则:
  - 所有输出统一为 AdvisorReport，下游消费方无感切换
  - 使用 Protocol 解耦，便于 mock 和替换
  - 数据模型 immutable（frozen dataclass），状态变更通过新建实例传递
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class AlertLevel(str, Enum):
    CRITICAL = "critical"      # 必须立即行动
    HIGH = "high"              # 强烈建议关注
    MEDIUM = "medium"          # 值得关注
    LOW = "low"                # 参考信息


class ActionType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    REDUCE = "reduce"          # 减仓（不卖完）
    ADD = "add"                # 加仓
    WATCH = "watch"            # 观望
    HEDGE = "hedge"            # 对冲


class PositionSize(str, Enum):
    FULL = "full"              # 重仓 ~80%
    HEAVY = "heavy"            # 较重 ~60%
    MODERATE = "moderate"      # 中等 ~40%
    LIGHT = "light"            # 轻仓 ~20%
    EMPTY = "empty"            # 空仓 0%


# ---------------------------------------------------------------------------
# 核心数据模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EventForecast:
    """事件预判 — 基于历史数据的事件影响预测."""
    event_name: str
    event_type: str
    scheduled_at: datetime
    impact_level: AlertLevel
    gold_direction: str          # up / down / uncertain
    expected_move_pct: float     # 预期波动幅度
    confidence: float            # 0~1
    historical_analogs: list[str] = field(default_factory=list)
    advice_summary: str = ""     # 一句话建议


@dataclass(frozen=True)
class ActionInstruction:
    """行动指令 — 具体可执行的投资操作."""
    action: ActionType
    position_size: PositionSize
    target_pct: float            # 建议仓位比例 0~1
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    urgency: str = "normal"      # immediate / high / normal / low
    reason: str = ""
    risk_note: str = ""
    doctrine_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SentimentReading:
    """市场情绪读数 — 多维度情绪指标快照."""
    retail_sentiment: str        # 散户情绪: greedy / fearful / neutral
    retail_extreme: bool         # 是否极端
    institutional_signal: str    # 机构方向: buying / selling / neutral
    cot_position: str            # COT 持仓: net_long / net_short / neutral
    etf_flow_signal: str         # ETF 资金流向
    fear_greed_index: float | None = None
    vix_level: float | None = None
    alignment_note: str = ""     # 与大资金节奏对齐建议


@dataclass(frozen=True)
class ExtremeStressTest:
    """极端情景压力测试结果."""
    scenario_name: str
    probability_estimate: float  # 主观概率
    max_drawdown_pct: float
    impact_duration_days: int
    hedge_recommendation: str = ""
    preparedness_score: float = 0.0  # 0~1，当前准备度


@dataclass(frozen=True)
class UserProfile:
    """用户画像 — 投资偏好与约束."""
    risk_tolerance: str = "medium"   # low / medium / high
    preferred_strategy: str = "balanced"
    current_position_pct: float = 0.0
    avg_cost: float = 0.0
    max_single_position_pct: float = 0.8
    allow_short: bool = False
    investment_horizon: str = "medium"  # short / medium / long


@dataclass(frozen=True)
class AdvisorReport:
    """顾问报告 — 所有 advisor 模块的统一输出格式."""
    report_type: str             # early_warning / action_guide / sentiment / extreme / consult
    timestamp: datetime = field(default_factory=datetime.now)

    # 通用字段（按需填充）
    alerts: list[EventForecast] = field(default_factory=list)
    instruction: ActionInstruction | None = None
    sentiment: SentimentReading | None = None
    stress_tests: list[ExtremeStressTest] = field(default_factory=list)
    consultation_answer: str = ""

    # 元数据
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.5
    warnings: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """渲染为 Markdown 格式."""
        lines: list[str] = []
        lines.append(f"## 顾问报告 — {self.report_type}")
        lines.append(f"**生成时间**: {self.timestamp.strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

        if self.alerts:
            lines.append("### 预警信息")
            for a in self.alerts:
                icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get(
                    a.impact_level.value, "⚪"
                )
                lines.append(f"{icon} **{a.event_name}** ({a.scheduled_at.strftime('%m-%d')})")
                lines.append(f"   预期影响: {a.gold_direction} ±{a.expected_move_pct:.1%} (置信度{a.confidence:.0%})")
                lines.append(f"   建议: {a.advice_summary}")
                lines.append("")

        if self.instruction:
            lines.append("### 行动指令")
            ins = self.instruction
            lines.append(f"**操作**: {ins.action.value.upper()} | **仓位**: {ins.position_size.value} ({ins.target_pct:.0%})")
            if ins.entry_price:
                lines.append(f"**入场价**: ${ins.entry_price:.2f}")
            if ins.stop_loss:
                lines.append(f"**止损**: ${ins.stop_loss:.2f}")
            if ins.take_profit:
                lines.append(f"**止盈**: ${ins.take_profit:.2f}")
            lines.append(f"**紧急度**: {ins.urgency}")
            lines.append(f"**理由**: {ins.reason}")
            if ins.risk_note:
                lines.append(f"**风险提示**: {ins.risk_note}")
            if ins.doctrine_refs:
                lines.append(f"**军规引用**: {', '.join(ins.doctrine_refs)}")
            lines.append("")

        if self.sentiment:
            lines.append("### 市场情绪")
            s = self.sentiment
            lines.append(f"- 散户情绪: {s.retail_sentiment} {'(极端!)' if s.retail_extreme else ''}")
            lines.append(f"- 机构动向: {s.institutional_signal}")
            lines.append(f"- COT 持仓: {s.cot_position}")
            lines.append(f"- ETF 流向: {s.etf_flow_signal}")
            if s.fear_greed_index is not None:
                lines.append(f"- 恐惧贪婪指数: {s.fear_greed_index:.0f}")
            if s.vix_level is not None:
                lines.append(f"- VIX: {s.vix_level:.2f}")
            if s.alignment_note:
                lines.append(f"**节奏对齐**: {s.alignment_note}")
            lines.append("")

        if self.stress_tests:
            lines.append("### 极端情景压力测试")
            for t in self.stress_tests:
                lines.append(f"- **{t.scenario_name}**: 最大回撤 {t.max_drawdown_pct:.1%}, "
                             f"概率估计 {t.probability_estimate:.0%}, 准备度 {t.preparedness_score:.0%}")
                if t.hedge_recommendation:
                    lines.append(f"  对冲建议: {t.hedge_recommendation}")
            lines.append("")

        if self.consultation_answer:
            lines.append("### 咨询回应")
            lines.append(self.consultation_answer)
            lines.append("")

        if self.warnings:
            lines.append("### ⚠️ 特别提醒")
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        if self.sources:
            lines.append(f"**数据来源**: {', '.join(self.sources)}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 协议接口 — 解耦各模块
# ---------------------------------------------------------------------------

class SignalProvider(Protocol):
    """信号提供方协议 — advisor 不依赖具体信号实现."""
    def generate_signals(self) -> Any: ...


class DataProvider(Protocol):
    """数据提供方协议."""
    def fetch_latest(self) -> Any: ...
