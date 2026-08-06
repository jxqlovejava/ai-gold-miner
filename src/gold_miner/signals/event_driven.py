"""明确信号管线 — 重大事件→结构化交易信号."""

from __future__ import annotations

import re
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
    # PPI 与 CPI 同逻辑: 通胀数据超预期 → 加息预期 → 利空黄金
    EventType.PPI: {
        "above_forecast": SignalDirection.BEARISH,
        "below_forecast": SignalDirection.BULLISH,
        "in_line": SignalDirection.NEUTRAL,
    },
    # PMI/ADP/ISM/消费者信心等经济数据: 超预期 → 经济强 → 加息预期 → 利空黄金
    EventType.PMI: {
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

    if event_type in (
        EventType.CPI,
        EventType.PCE,
        EventType.NFP,
        EventType.PPI,
        EventType.PMI,
    ):
        a = _extract_number(actual)
        f = _extract_number(forecast)
        if a is not None and f is not None:
            if a > f:
                return SignalDirection.BEARISH
            if a < f:
                return SignalDirection.BULLISH
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

            now = datetime.now(tz=event.scheduled_at.tzinfo) if event.scheduled_at.tzinfo else datetime.now()
            days_until = (event.scheduled_at - now).days
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
            if direction == SignalDirection.BULLISH:
                score = base_score + surprise_bonus
            elif direction == SignalDirection.BEARISH:
                score = -(base_score + surprise_bonus)
            else:
                # NEUTRAL（数据事件未出方向/无法解析）：不给正负分，避免系统性拉低 event 维度
                score = 0.0

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

    def generate_post_event_signals_from_calendar(
        self,
        lookback_days: int = 7,
    ) -> list[Signal]:
        """从日历中自动发现已出结果的事件，生成 post-event 信号.

        与 generate_post_event_signals 的区别：
        - 前者需要调用方手动传入 (event, actual, forecast) 元组列表
        - 本方法自动从 EventCalendar 中查询有 actual 值的近期事件

        用于管线自动注入：第〇步同步事件结果后，本方法自动将
        「预期 vs 实际偏差」转化为方向信号。

        对 fast-evolving 事件的过时数据自动降级处理。
        """
        events_with_results = self.calendar.get_recent_events_with_results(
            lookback_days=lookback_days,
        )
        if not events_with_results:
            return []

        tuples: list[tuple[CalendarEvent, str, str]] = []
        for event in events_with_results:
            actual = event.actual or ""
            forecast = event.forecast or ""
            if not actual:
                continue
            tuples.append((event, actual, forecast))

        signals = self.generate_post_event_signals(tuples)

        # 对 fast-evolving 过时事件降级: STRONG→MODERATE→WEAK
        # 信号索引与 events 一一对应 (generate_post_event_signals 保持顺序)
        for i, sig in enumerate(signals):
            event = events_with_results[i] if i < len(events_with_results) else None
            if event and event.needs_reverify:
                signals[i] = Signal(
                    name=sig.name,
                    dimension=sig.dimension,
                    direction=sig.direction,
                    strength=SignalStrength.WEAK,
                    score=sig.score * 0.5,
                    description=sig.description + " (数据可能已过时, 待重新验证)",
                    timestamp=sig.timestamp,
                    metadata={**sig.metadata, "staleness_risk": True},
                )

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
        a = _extract_number(actual)
        f = _extract_number(forecast)
        if a is None or f is None or f == 0:
            return 0.0
        deviation = abs(a - f) / abs(f)
        return min(deviation * 0.5, 0.5)


_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_UNIT_MULT = {"万": 1e4, "亿": 1e8, "K": 1e3, "M": 1e6, "%": 1.0}


def _extract_number(text: str) -> float | None:
    """从事件结果文本中提取第一个数值，并换算中文/英文单位.

    事件 actual/forecast 常混入描述文本（如 "实际 +4.4万 (预期6.5-7.5万...)"），
    直接 float() 会抛 ValueError。本函数用正则取首个数值并按后缀单位换算：
    - "实际 +4.4万" → 44000.0
    - "55.6(预期54.0)" → 55.6
    - "6.5万" → 65000.0；"3.3%" → 3.3
    """
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    val = float(m.group())
    tail = text[m.end():]
    for unit, mult in _UNIT_MULT.items():
        if tail.startswith(unit):
            return val * mult
    return val


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

    if event_type in (
        EventType.CPI,
        EventType.PCE,
        EventType.NFP,
        EventType.PPI,
        EventType.PMI,
    ):
        a = _extract_number(actual)
        f = _extract_number(forecast)
        if a is None or f is None:
            return "in_line"
        if a > f * 1.005:
            return "above_forecast"
        if a < f * 0.995:
            return "below_forecast"
        return "in_line"

    return "in_line"
