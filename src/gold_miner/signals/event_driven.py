"""明确信号管线 — 重大事件→结构化交易信号."""

from dataclasses import dataclass
from datetime import datetime

from gold_miner.data.calendar import CalendarEvent, EventCalendar, EventImpact, EventType
from gold_miner.signals.base import Signal, SignalDirection, SignalStrength

# ---------------------------------------------------------------------------
# 事件→黄金方向映射
# ---------------------------------------------------------------------------

EVENT_DIRECTION_MAP: dict[EventType, dict[str, SignalDirection]] = {
    EventType.FED_RATE: {
        "hike": SignalDirection.BEARISH,
        "cut": SignalDirection.BULLISH,
        "hold": SignalDirection.NEUTRAL,
    },
    EventType.CPI: {
        "above_forecast": SignalDirection.BEARISH,
        "below_forecast": SignalDirection.BULLISH,
        "in_line": SignalDirection.NEUTRAL,
    },
    EventType.PCE: {
        "above_forecast": SignalDirection.BEARISH,
        "below_forecast": SignalDirection.BULLISH,
        "in_line": SignalDirection.NEUTRAL,
    },
    EventType.NFP: {
        "above_forecast": SignalDirection.BEARISH,
        "below_forecast": SignalDirection.BULLISH,
        "in_line": SignalDirection.NEUTRAL,
    },
    EventType.FOMC_MINUTES: {
        "hawkish": SignalDirection.BEARISH,
        "dovish": SignalDirection.BULLISH,
        "neutral": SignalDirection.NEUTRAL,
    },
    EventType.GEO_POLITICAL: {
        "escalation": SignalDirection.BULLISH,
        "deescalation": SignalDirection.BEARISH,
    },
}


def _infer_event_direction(
    event_type: EventType,
    outcome: str,
    forecast: str | None = None,
    actual: str | None = None,
) -> SignalDirection:
    """从事件类型+结果推演方向."""
    mapping = EVENT_DIRECTION_MAP.get(event_type, {})
    if outcome in mapping:
        return mapping[outcome]

    if event_type in (EventType.CPI, EventType.PCE, EventType.NFP):
        if actual and forecast:
            try:
                a, f = float(actual.replace("%", "")), float(forecast.replace("%", ""))
                if a > f:
                    return SignalDirection.BEARISH
                if a < f:
                    return SignalDirection.BULLISH
                return SignalDirection.NEUTRAL
            except ValueError:
                pass
        return SignalDirection.NEUTRAL

    return SignalDirection.NEUTRAL


# ---------------------------------------------------------------------------
# 事件信号模型
# ---------------------------------------------------------------------------


@dataclass
class EventSignal:
    event: CalendarEvent
    signal_type: str  # pre_event | post_event
    expected_direction: SignalDirection
    actual_direction: SignalDirection | None = None
    pre_event_score: float = 0.0
    post_event_score: float = 0.0
    market_reaction: str = ""  # priced_in | surprise | non_reaction
    confidence: float = 0.5
    description: str = ""


# ---------------------------------------------------------------------------
# 事件驱动信号生成器
# ---------------------------------------------------------------------------


class EventDrivenSignalGenerator:
    """基于经济日历事件生成交易信号."""

    IMPACT_SCORE_MAP = {
        EventImpact.HIGH: 0.7,
        EventImpact.MEDIUM: 0.4,
        EventImpact.LOW: 0.15,
    }

    def __init__(self, calendar: EventCalendar | None = None) -> None:
        self.calendar = calendar or EventCalendar()

    def generate_pre_event_signals(self, days_ahead: int = 7) -> list[Signal]:
        """生成事件前的预警信号."""
        signals: list[Signal] = []
        upcoming = self.calendar.get_upcoming(days=days_ahead)

        for event in sorted(upcoming, key=lambda e: e.impact.value, reverse=True):
            base_score = self.IMPACT_SCORE_MAP.get(event.impact, 0.1)
            direction = self._pre_event_direction(event)

            if direction == SignalDirection.NEUTRAL:
                continue

            days_until = (event.scheduled_at - datetime.now()).days
            urgency_bonus = max(0, (7 - days_until) / 7 * 0.15)
            score = base_score + urgency_bonus

            if direction == SignalDirection.BEARISH:
                score = -score

            signals.append(Signal(
                name=f"事件预警: {event.name}",
                dimension="event",
                direction=direction,
                strength=(
                    SignalStrength.STRONG if event.impact == EventImpact.HIGH and days_until <= 3
                    else SignalStrength.MODERATE
                ),
                score=round(max(-1.0, min(1.0, score)), 2),
                description=(
                    f"{event.name} 预计 {event.scheduled_at.strftime('%m-%d %H:%M')}"
                    f"({'高' if event.impact == EventImpact.HIGH else '中'}影响)"
                    f"{'，临近事件' if days_until <= 2 else ''}"
                ),
                metadata={
                    "event_name": event.name,
                    "event_type": event.event_type.value,
                    "scheduled_at": event.scheduled_at.isoformat(),
                    "days_until": days_until,
                    "impact": event.impact.value,
                },
            ))

        return signals

    def generate_post_event_signals(
        self,
        events_with_outcomes: list[tuple[CalendarEvent, str, str]],
    ) -> list[Signal]:
        """事件发生后，比较预期vs实际生成信号."""
        signals: list[Signal] = []

        for event, actual_value, forecast_value in events_with_outcomes:
            outcome = _classify_outcome(event.event_type, actual_value, forecast_value)
            base_score = self.IMPACT_SCORE_MAP.get(event.impact, 0.1)
            direction = _infer_event_direction(
                event.event_type, outcome,
                forecast=forecast_value, actual=actual_value,
            )

            surprise_bonus = self._surprise_magnitude(event.event_type, actual_value, forecast_value)
            score = (base_score + surprise_bonus) * (1 if direction == SignalDirection.BULLISH else -1)

            signals.append(Signal(
                name=f"事件结果: {event.name}",
                dimension="event",
                direction=direction,
                strength=(
                    SignalStrength.STRONG if surprise_bonus > 0.2
                    else SignalStrength.MODERATE
                ),
                score=round(max(-1.0, min(1.0, score)), 2),
                description=(
                    f"{event.name}: 实际 {actual_value} vs 预期 {forecast_value}"
                    f"({'超预期' if surprise_bonus > 0.1 else '符合预期'})"
                ),
                metadata={
                    "event_name": event.name,
                    "event_type": event.event_type.value,
                    "actual": actual_value,
                    "forecast": forecast_value,
                    "surprise": round(surprise_bonus, 3),
                },
            ))

        return signals

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _pre_event_direction(self, event: CalendarEvent) -> SignalDirection:
        """预判事件前市场方向.

        对于黄金而言：
        - 加息预期 → 美元走强 → 黄金承压（利空）
        - 降息预期 → 美元走弱 → 黄金受益（利好）
        - 地缘紧张 → 避险需求 → 利好
        - 数据事件 → 中性（等实际结果）
        """
        if event.event_type == EventType.FED_RATE:
            if event.forecast and "cut" in event.forecast.lower():
                return SignalDirection.BULLISH
            if event.forecast and ("hike" in event.forecast.lower() or "raise" in event.forecast.lower()):
                return SignalDirection.BEARISH
            return SignalDirection.NEUTRAL

        if event.event_type == EventType.GEO_POLITICAL:
            return SignalDirection.BULLISH

        if event.event_type in (EventType.CPI, EventType.PCE, EventType.NFP):
            # 数据事件事前方向中性
            return SignalDirection.NEUTRAL

        if event.event_type == EventType.FOMC_MINUTES:
            return SignalDirection.NEUTRAL

        return SignalDirection.NEUTRAL

    @staticmethod
    def _surprise_magnitude(
        event_type: EventType,
        actual: str,
        forecast: str,
    ) -> float:
        """计算预期偏差幅度."""
        try:
            a = float(actual.replace("%", "").replace("K", "").replace("M", ""))
            f = float(forecast.replace("%", "").replace("K", "").replace("M", ""))
            if f == 0:
                return 0.0
            deviation = abs(a - f) / abs(f)
            return min(deviation * 0.5, 0.5)
        except (ValueError, AttributeError):
            return 0.0


def _classify_outcome(
    event_type: EventType,
    actual: str,
    forecast: str,
) -> str:
    """将事件结果分类为 hike/cut/above_forecast 等."""
    if event_type == EventType.FED_RATE:
        try:
            a = float(actual.replace("%", ""))
            f = float(forecast.replace("%", "")) if forecast else 0
            if a > f:
                return "hike"
            if a < f:
                return "cut"
            return "hold"
        except ValueError:
            return "hold"

    if event_type == EventType.FOMC_MINUTES:
        text = (actual + forecast).lower()
        if any(w in text for w in ("hawkish", "tighten", "hike")):
            return "hawkish"
        if any(w in text for w in ("dovish", "ease", "cut")):
            return "dovish"
        return "neutral"

    if event_type == EventType.GEO_POLITICAL:
        text = (actual + forecast).lower()
        if any(w in text for w in ("escalation", "attack", "war", "strike", "conflict")):
            return "escalation"
        return "deescalation"

    if event_type in (EventType.CPI, EventType.PCE, EventType.NFP):
        try:
            a = float(actual.replace("%", ""))
            f = float(forecast.replace("%", "")) if forecast else 0
            if a > f * 1.005:
                return "above_forecast"
            if a < f * 0.995:
                return "below_forecast"
            return "in_line"
        except ValueError:
            return "in_line"

    return "in_line"
